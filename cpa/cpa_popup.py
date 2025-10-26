import matplotlib.pyplot as mpl
import mplcursors
import numpy as np

import cpa.cpa_line as cpa_l

class CpaPopUp():
    '''Open a popup window with a focus at a set of lines.'''

    def __init__(self, id: int):
        self.id = id
        self.lines = None
        self.is_open = False

        self._fig = None
        self._fig_name = f'Plot: {id}'
        # TODO: Set limit sign based upon where home is.
        self._x_sign = -1
        self._y_sign = -1

        #self.ax.invert_xaxis()
        #self.ax.invert_yaxis()
        self._last_annotation = None

        self._plot_lines = []
        self._cpa_lines: dict[int, cpa_l.CpaLine] = {}

        # For moves relative to a set origin
        self._origin_x = 0
        self._origin_y = 0

        self._max_win_x = None
        self._min_win_x = None
        self._max_win_y = None
        self._min_win_y = None

    def _plot_rel_x(self, x):
        return x * self._x_sign

    def _plot_rel_y(self, y):
        return y * self._y_sign

    def _add_line(self, cpa_line: cpa_l.CpaLine):
        '''Add a line to the plot.'''
        _start_x, _start_y = cpa_line.start
        _end_x, _end_y = cpa_line.end

        _s_x = self._plot_rel_x(_start_x)
        _s_y = self._plot_rel_y(_start_y)
        _e_x = self._plot_rel_x(_end_x)
        _e_y = self._plot_rel_y(_end_y)

        # Determine window dimensions
        if self._max_win_x is None:
            self._max_win_x = max(_e_x, _s_x)
        if self._min_win_x is None:
            self._min_win_x = min(_e_x, _s_x)
        if self._max_win_y is None:
            self._max_win_y = max(_e_y, _s_y)
        if self._min_win_y is None:
            self._min_win_y = min(_e_y, _s_y)
        self._max_win_x = max(_e_x, self._max_win_x)
        self._min_win_x = min(_e_x, self._min_win_x)
        self._max_win_y = max(_e_y, self._max_win_y)
        self._min_win_y = min(_e_y, self._min_win_y)

        # Set the new coordinate -- good or bad.
        _lw = cpa_line.width
        _c = cpa_line.color
        _ls = cpa_line.style
        _ax_line = self._ax.plot(
            [_s_x, _e_x], [_s_y, _e_y],
            label=f'{cpa_line.cmd_id}:{cpa_line.command}',
            color=_c, lw=_lw, linestyle=_ls
        )
        self._plot_lines.append(_ax_line)
        self._cpa_lines[cpa_line.cmd_id] = cpa_line # For sanity check.

    def savefig(self, out_file: str):
        '''Save the plot image.'''
        self._fig.savefig(out_file)

    def show(self, cpa_lines: dict[int, cpa_l.CpaLine]):
        '''Display the popup window.'''
        def _annotate(sel: mplcursors._pick_info.Selection):
            sel.annotation.draggable(False)
            sel.annotation.set_visible(False)
            sel.annotation.set_fontsize(6)
            _line = sel.artist
            # Ruida controllers have home far right. This effectively
            # reverses coordinates so that positive becomes negative.
            _end_x = _line.get_xdata()[-1]
            _min_x, _max_x = (self._ax.get_xlim())
            # Move the plot window if the new coord is not in the visible area.
            _len_x = _max_x - _min_x
            if _end_x < _min_x:
                _min = _end_x - _len_x / 2
                _max = _end_x + _len_x / 2
                self._ax.set_xlim(_min, _max)
                _min_x, _max_x = (self._ax.get_xlim())

            if _end_x < _min_x:
                _pos_x = _min_x
            else:
                _pos_x = _end_x

            _end_y = _line.get_ydata()[-1]
            _min_y, _max_y = self._ax.get_ylim()
            # Move the plot window if the new coord is not in the visible area.
            _len_y = _max_y - _min_y
            if _end_y < _min_y:
                _min = _end_y - _len_y / 2
                _max = _end_y + _len_y / 2
                self._ax.set_ylim(_min, _max)
                _min_y, _max_y = self._ax.get_ylim()

            if _end_y < _min_y:
                _pos_y = _min_y
            else:
                _pos_y = _end_y

            _label: str = _line.get_label()
            _cmd_id = int(_label.split(':')[0])
            if self._last_annotation is not None:
                self._last_annotation.remove()

            self._last_annotation = self._ax.annotate(
                self._cpa_lines[_cmd_id].annotation,
                xy=(_pos_x, _pos_y),
                xytext=(5, 5),
                textcoords='offset points',
                bbox=dict(
                    boxstyle='round,pad=0.5',
                    fc='yellow',
                    ec='black',
                    lw=1,
                    alpha=0.6,
                    ),
                arrowprops=dict(
                    arrowstyle='->', connectionstyle='arc3,rad=.2',
                ),
                ha='center', va='bottom',
                fontsize=6,
            )
            self._last_annotation.draggable()
        # End _annotate

        self._cpa_lines = cpa_lines
        _cpa_line: cpa_l.CpaLine = self._cpa_lines[next(iter(self._cpa_lines))]
        self._plot_title = f'{_cpa_line.cmd_id}:{_cpa_line.command}'
        self._fig, self._ax = mpl.subplots(
            num=self._fig_name, figsize=(10, 8.5), linewidth=1)
        self._fig.suptitle(self._plot_title)
        self._ax.set_xlabel('Bed X mm')
        self._ax.set_ylabel('Bed Y mm')
        self._ax.set_aspect('equal')
        self._ax.grid(True)

        for _l in self._cpa_lines:
            self._add_line(self._cpa_lines[_l])
        self._ax.set_xlim(self._min_win_x - 5,
                            self._max_win_x + 5)
        self._ax.set_ylim(self._min_win_y - 5,
                            self._max_win_y + 5)
        self._ax.legend(
            fontsize=6,
            fancybox=True,
            shadow=True,
            draggable=True,
            )
        self._fig.show()
        self._fig.canvas.draw_idle()
        _lines = [self._plot_lines[0]]
        cursor = mplcursors.cursor(self._ax, hover=True)
        # cursor = mplcursors.cursor(_lines, hover=True, multiple=False)
        cursor.connect('add', _annotate)
        self.is_open = True
        self._fig.canvas.mpl_connect('close_event', self.close)

    def close(self, event=None):
        self.is_open = False
        mpl.close(self._fig_name)
