'''Message emitter for the Ruida Protocol Analyzer'''

class RdaEmitter():
    '''
    A class to write messages to the output file and console.

    Some message actions will trigger exceptions and a shutdown.

    '''
    def __init__(self, args):
        self.args = args
        self._out_fp = None

    def open(self):
        if self.args.output_file:
            self._out_fp = open(self.args.output_file, 'w')

    def close(self):
        if self._out_fp is not None:
            self._out_fp.close()

    def write(self, message: str):
        '''A write method to emulate an output file for the analyzer.

        The analyzer calls this method while decoding the input file. This
        then writes to the console or the output file.'''
        _msg = '\n' + message
        if self._out_fp is not None:
            self._out_fp.write(_msg)
        if not self.args.quiet:
            print(_msg)

    def reader(self, message: str):
        '''Emit packet information messages from a reader.'''
        self.write('PRT:RDR:' + message)

    def parser(self, message: str):
        '''Emit a message related to parsing the incoming data.'''
        self.write('PRT:PRS:' + message)

    def error(self, message: str):
        '''Emit error messages related to the incoming stream.'''
        self.write('PRT:ERR:' + message)
        if self.args.stop_on_error:
            raise SyntaxError('Stopping...')

    def shutdown(self, message: str):
        '''This is used to shutdown the analyzer when an uncorrectable error
        is detected (cannot sync).'''
        self.write('PRT:FTL:' + message)
        raise SyntaxError('Stopping...')

    def verbose(self, message: str):
        '''Emit verbose messages.'''
        if self.args.verbose:
            self.write('PRT:vrb:' + message)

    # Internal messages.
    def protocol(self, message: str):
        '''A problem with the protocol definitions or state machines.'''
        _msg = 'INT:FTL:' + message
        if self.args.stop_on_error:
            raise SyntaxError(_msg)
        else:
            self.write(_msg)

    def info(self, message: str):
        '''Emit message related to the protocol analyzer and parser.'''
        self.write('INT:INF:' + message)

    def warn(self, message: str):
        '''A warning about a correctable error.'''
        self.write('INT:WRN:' + message)

    def critical(self, message: str):
        '''A critical error has occurred -- will continue to run.'''
        self.write('INT:CRT:' + message)

    def fatal(self, message: str):
        '''A fatal error has occurred and shutting down.'''
        raise RuntimeError('INT:FTL:' + message)
