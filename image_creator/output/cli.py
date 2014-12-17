# -*- coding: utf-8 -*-
#
# Copyright (C) 2011-2014 GRNET S.A.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Normal Command-line interface output"""

from image_creator.output import Output

import sys
from colors import red, green, yellow
from progress.bar import Bar


def write(msg, new_line, decorate, stream):
    """Print a message"""
    nl = "\n" if new_line else ' '
    stream.write(decorate(msg) + nl)


class SilentOutput(Output):
    """Silent Output class. Only Errors are printed"""

    def __init__(self, colored=True, stream=None):
        self.colored = colored
        self.stream = sys.stderr if stream is None else stream

    def error(self, msg, new_line=True):
        """Print an error"""
        color = red if self.colored else lambda x: x
        write("Error: %s" % msg, new_line, color, self.stream)


class SimpleOutput(SilentOutput):
    """Print messages but not progress bars. Progress bars are treated as
    output messages. The user gets informed when the action begins and when it
    ends, but no progress is shown in between."""

    def warn(self, msg, new_line=True):
        """Print a warning"""
        color = yellow if self.colored else lambda x: x
        write("Warning: %s" % msg, new_line, color, self.stream)

    def success(self, msg, new_line=True):
        """Print msg after an action is completed"""
        color = green if self.colored else lambda x: x
        write(msg, new_line, color, self.stream)

    def info(self, msg='', new_line=True):
        """Print msg as normal program output"""
        write(msg, new_line, lambda x: x, self.stream)

    def clear(self):
        """Clear the screen"""
        if self.stream.isatty():
            self.stream.write('\033[H\033[2J')


class OutputWthProgress(SimpleOutput):
    """Output class with progress."""
    class _Progress(Bar):
        MESSAGE_LENGTH = 30

        template = {
            'default': '%(index)d/%(max)d',
            'percent': '%(percent)d%%',
            'b': '%(index)d/%(max)d B',
            'kb': '%(index)d/%(max)d KB',
            'mb': '%(index)d/%(max)d MB'
        }

        def __init__(self, size, title, bar_type='default'):
            """Create a Progress bar"""
            self.hide_cursor = False
            super(OutputWthProgress._Progress, self).__init__()
            self.title = title
            self.fill = '#'
            self.bar_prefix = ' ['
            self.bar_suffix = '] '
            self.message = ("%s:" % self.title).ljust(self.MESSAGE_LENGTH)
            self.suffix = self.template[bar_type]
            self.max = size

            # print empty progress bar
            self.start()

        def success(self, result):
            """Print result after progress has finished"""
            self.parent.info("\r%s ...\033[K" % self.title, False)
            self.parent.success(result)

# vim: set sta sts=4 shiftwidth=4 sw=4 et ai :
