class CpaLine():
    '''A line representing a move of the virtual head.

    '''
    def __init__(self,
                 cmd_id, cmd_label, index, start, end, speed,
                 power, width, style, color):
        self.cmd_id = cmd_id # For sanity.
        self.command = cmd_label
        self.index = index
        self.start = start
        self.end = end
        self.speed = speed
        self.power = power
        self.width = width
        self.style = style
        self.color = color

    def __str__(self):
        return (
            f'\nCmd ID: {self.cmd_id}'
            f'\nCommand: {self.command}'
            f'\nIndex: {self.index}'
            f'\nStart: {self.start}'
            f'\nEnd: {self.end}'
            f'\nSpeed: {self.speed}'
            f'\nPower: {self.power}'
            f'\nLine width: {self.width}'
            f'\nLine style: {self.style}'
            f'\nColor: {self.color}'
             ' #'.join(f'{int(_c * 255):02X}' for _c in self.color)
        )
