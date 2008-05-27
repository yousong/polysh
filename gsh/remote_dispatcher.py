# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Library General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
#
# See the COPYING file for license information.
#
# Copyright (c) 2006, 2007, 2008 Guillaume Chazarain <guichaz@gmail.com>

import asyncore
import os
import pty
import signal
import sys
import termios

from gsh.buffered_dispatcher import buffered_dispatcher
from gsh import callbacks
from gsh.console import console_output

# Either the remote shell is expecting a command or one is already running
STATE_NAMES = ['not_started', 'idle', 'running', 'terminated']

STATE_NOT_STARTED,         \
STATE_IDLE,                \
STATE_RUNNING,             \
STATE_TERMINATED = range(len(STATE_NAMES))

# Count the total number of remote_dispatcher.handle_read() invocations
nr_handle_read = 0

def main_loop_iteration(timeout=None):
    """Return the number of remote_dispatcher.handle_read() calls made by this
    iteration"""
    prev_nr_read = nr_handle_read
    asyncore.loop(count=1, timeout=timeout, use_poll=True)
    return nr_handle_read - prev_nr_read

def log(msg):
    if options.log_file:
        options.log_file.write(msg)

class remote_dispatcher(buffered_dispatcher):
    """A remote_dispatcher is a ssh process we communicate with"""

    def __init__(self, hostname):
        self.pid, fd = pty.fork()
        if self.pid == 0:
            # Child
            self.launch_ssh(hostname)
            sys.exit(1)

        # Parent
        buffered_dispatcher.__init__(self, fd)
        self.hostname = hostname
        self.debug = options.debug
        self.active = True # deactived shells are dead forever
        self.enabled = True # shells can be enabled and disabled
        self.state = STATE_NOT_STARTED
        self.term_size = (-1, -1)
        self.display_name = ''
        self.change_name(hostname)
        self.init_string = self.configure_tty() + self.set_prompt()
        self.init_string_sent = False
        self.read_in_state_not_started = ''
        self.command = options.command

    def launch_ssh(self, name):
        """Launch the ssh command in the child process"""
        evaluated = options.ssh % {'host': name}
        if evaluated == options.ssh:
            evaluated = '%s %s' % (evaluated, name)
        os.execlp('/bin/sh', 'sh', '-c', evaluated)

    def set_enabled(self, enabled):
        from gsh.dispatchers import update_max_display_name_length
        self.enabled = enabled
        if options.interactive:
            # In non-interactive mode, remote processes leave as soon
            # as they are terminated, but we don't want to break the
            # indentation if all the remaining processes have short names.
            l = len(self.display_name)
            if not enabled:
                l = -l
            update_max_display_name_length(l)

    def change_state(self, state):
        """Change the state of the remote process, logging the change"""
        if state is not self.state:
            if self.debug:
                self.print_debug('state => %s' % (STATE_NAMES[state]))
            if self.state == STATE_NOT_STARTED:
                self.read_in_state_not_started = ''
            self.state = state

    def disconnect(self):
        """We are no more interested in this remote process"""
        try:
            os.kill(self.pid, signal.SIGKILL)
        except OSError:
            # The process was already dead, no problem
            pass
        self.read_buffer = ''
        self.write_buffer = ''
        self.active = False
        self.set_enabled(False)
        if self.read_in_state_not_started:
            self.print_lines(self.read_in_state_not_started)
            self.read_in_state_not_started = ''
        if options.abort_error and self.state is STATE_NOT_STARTED:
            raise asyncore.ExitNow(1)

    def reconnect(self):
        """Relaunch and reconnect to this same remote process"""
        self.disconnect()
        self.close()
        remote_dispatcher(self.hostname)

    def configure_tty(self):
        """We don't want \n to be replaced with \r\n, and we disable the echo"""
        attr = termios.tcgetattr(self.fd)
        attr[1] &= ~termios.ONLCR # oflag
        attr[3] &= ~termios.ECHO # lflag
        termios.tcsetattr(self.fd, termios.TCSANOW, attr)
        # unsetopt zle prevents Zsh from resetting the tty
        return 'unsetopt zle 2> /dev/null;stty -echo -onlcr;'

    def seen_prompt_cb(self, unused):
        if options.interactive:
            self.change_state(STATE_IDLE)
        elif self.command:
            p1, p2 = callbacks.add('real prompt ends', lambda d: None, True)
            self.dispatch_command('PS1="%s""%s\n"\n' % (p1, p2))
            self.dispatch_command(self.command + '\n')
            self.dispatch_command(self.init_string)
            self.command = None
        else:
            self.change_state(STATE_TERMINATED)
            self.disconnect()

    def set_prompt(self):
        """The prompt is important because we detect the readyness of a process
        by waiting for its prompt."""
        # No right prompt
        command_line = 'RPS1=;RPROMPT=;'
        command_line += 'TERM=ansi;'
        command_line += 'unset HISTFILE;'
        prompt1, prompt2 = callbacks.add('prompt', self.seen_prompt_cb, True)
        command_line += 'PS1="%s""%s\n"\n' % (prompt1, prompt2)
        return command_line

    def readable(self):
        """We are always interested in reading from active remote processes if
        the buffer is OK"""
        return self.active and buffered_dispatcher.readable(self)

    def handle_error(self):
        """An exception may or may not lead to a disconnection"""
        if buffered_dispatcher.handle_error(self):
            console_output('Error talking to %s\n' % self.display_name)
            self.disconnect()

    def print_lines(self, lines):
        from gsh.dispatchers import max_display_name_length
        lines = lines.strip('\n')
        while True:
            no_empty_lines = lines.replace('\n\n', '\n')
            if len(no_empty_lines) == len(lines):
                break
            lines = no_empty_lines
        if not lines:
            return
        indent = max_display_name_length - len(self.display_name)
        prefix = self.display_name + indent * ' ' + ': '
        console_output(prefix + lines.replace('\n', '\n' + prefix) + '\n')

    def handle_read_fast_case(self, data):
        """If we are in a fast case we'll avoid the long processing of each
        line"""
        if self.state is not STATE_RUNNING or callbacks.any_in(data):
            # Slow case :-(
            return False

        last_nl = data.rfind('\n')
        if last_nl == -1:
            # No '\n' in data => slow case
            return False
        self.read_buffer = data[last_nl + 1:]
        self.print_lines(data[:last_nl])
        return True

    def handle_read(self):
        """We got some output from a remote shell, this is one of the state
        machine"""
        if not self.active:
            return
        global nr_handle_read
        nr_handle_read += 1
        new_data = buffered_dispatcher.handle_read(self)
        if self.debug:
            self.print_debug('==> ' + new_data)
        if self.handle_read_fast_case(self.read_buffer):
            return
        lf_pos = new_data.find('\n')
        if lf_pos >= 0:
            # Optimization: we knew there were no '\n' in the previous read
            # buffer, so we searched only in the new_data and we offset the
            # found index by the length of the previous buffer
            lf_pos += len(self.read_buffer) - len(new_data)
        while lf_pos >= 0:
            # For each line in the buffer
            line = self.read_buffer[:lf_pos + 1]
            if callbacks.process(line):
                pass
            elif self.state in (STATE_IDLE, STATE_RUNNING):
                self.print_lines(line)
            elif self.state is STATE_NOT_STARTED:
                self.read_in_state_not_started += line
                if 'The authenticity of host' in line:
                    msg = line.strip('\n') + ' Closing connection.'
                    self.disconnect()
                elif 'REMOTE HOST IDENTIFICATION HAS CHANGED' in line:
                    msg = 'Remote host identification has changed.'
                else:
                    msg = None

                if msg:
                    self.print_lines(msg + ' Consider manually connecting or ' +
                                     'using ssh-keyscan.')

            # Go to the next line in the buffer
            self.read_buffer = self.read_buffer[lf_pos + 1:]
            if self.handle_read_fast_case(self.read_buffer):
                return
            lf_pos = self.read_buffer.find('\n')
        if self.state is STATE_NOT_STARTED and not self.init_string_sent:
            self.dispatch_write(self.init_string)
            self.init_string_sent = True

    def print_unfinished_line(self):
        """The unfinished line stayed long enough in the buffer to be printed"""
        if self.state is STATE_RUNNING:
            if not callbacks.process(self.read_buffer):
                self.print_lines(self.read_buffer)
            self.read_buffer = ''

    def writable(self):
        """Do we want to write something?"""
        return self.active and buffered_dispatcher.writable(self)

    def handle_write(self):
        """Let's write as much as we can"""
        num_sent = self.send(self.write_buffer)
        if self.debug:
            self.print_debug('<== ' + self.write_buffer[:num_sent])
        self.write_buffer = self.write_buffer[num_sent:]

    def print_debug(self, msg):
        """Log some debugging information to the console"""
        state = STATE_NAMES[self.state]
        msg = msg.encode('string_escape')
        console_output('[dbg] %s[%s]: %s\n' % (self.display_name, state, msg))

    def get_info(self):
        """Return a list with all information available about this process"""
        if self.active:
            state = STATE_NAMES[self.state]
        else:
            state = ''

        if self.debug:
            debug = 'debug'
        else:
            debug = ''

        return [self.display_name, 'fd:%d' % (self.fd),
                'r:%d' % (len(self.read_buffer)),
                'w:%d' % (len(self.write_buffer)),
                self.active and 'active' or 'dead',
                self.enabled and 'enabled' or 'disabled',
                state,
                debug]

    def dispatch_write(self, buf):
        """There is new stuff to write when possible"""
        if self.active and self.enabled:
            buffered_dispatcher.dispatch_write(self, buf)
            return True

    def dispatch_command(self, command):
        if self.dispatch_write(command):
            self.change_state(STATE_RUNNING)

    def change_name(self, name):
        """Change the name of the shell, possibly updating the maximum name
        length"""
        from gsh import dispatchers
        if not name:
            name = self.hostname
        previous_name_len = len(self.display_name)
        self.display_name = None
        self.display_name = dispatchers.make_unique_name(name)
        dispatchers.update_max_display_name_length(len(self.display_name))
        dispatchers.update_max_display_name_length(-previous_name_len)

    def rename(self, string):
        """Send to the remote shell, its new name to be shell expanded"""
        if string:
            rename1, rename2 = callbacks.add('rename', self.change_name, False)
            self.dispatch_command('/bin/echo "%s""%s"%s\n' %
                                                     (rename1, rename2, string))
        else:
            self.change_name(self.hostname)

    def __str__(self):
        return self.display_name
