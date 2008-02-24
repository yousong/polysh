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
import glob
import os

from gsh.control_commands_helpers import complete_shells, selected_shells
from gsh.control_commands_helpers import list_control_commands
from gsh.control_commands_helpers import get_control_command, toggle_shells
from gsh.control_commands_helpers import expand_local_path
from gsh.completion import complete_local_absolute_path
from gsh import dispatchers
from gsh import remote_dispatcher
from gsh import stdin
from gsh import file_transfer

def complete_help(line, text):
    colon = text.startswith(':')
    text = text.lstrip(':')
    res = [cmd + ' ' for cmd in list_control_commands() if \
                           cmd.startswith(text) and ' ' + cmd + ' ' not in line]
    if colon:
        res = [':' + cmd for cmd in res]
    return res

def do_help(command):
    """
    Usage: :help [COMMAND]
    List control commands or show their documentations.
    """
    command = command.strip()
    if command:
        texts = []
        for name in command.split():
            try:
                cmd = get_control_command(name.lstrip(':'))
            except AttributeError:
                print 'Unknown control command:', name
            else:
                doc = [d.strip() for d in cmd.__doc__.split('\n') if d.strip()]
                texts.append('\n'.join(doc))
        print '\n\n'.join(texts)
    else:
        names = list_control_commands()
        max_name_len = max(map(len, names))
        for i in xrange(len(names)):
            name = names[i]
            txt = (max_name_len - len(name)) * ' ' + ':' + name + ' - '
            doc = get_control_command(name).__doc__
            txt += doc.split('\n')[2].strip()
            print txt

def complete_list(line, text):
    return complete_shells(line, text)

def do_list(command):
    """
    Usage: :list [SHELLS...]
    List remote shells and their states.
    The special characters * ? and [] work as expected.
    """
    nr_active = nr_dead = 0
    instances = []
    for i in selected_shells(command):
        instances.append(i.get_info())
        if i.active:
            nr_active += 1
        else:
            nr_dead += 1
    dispatchers.format_info(instances)
    print '%s\n\n%d active shells, %d dead shells, total: %d' % \
           ('\n'.join(instances), nr_active, nr_dead, nr_active + nr_dead)

def do_quit(command):
    """
    Usage: :quit
    Quit gsh.
    """
    raise asyncore.ExitNow(0)

def complete_chdir(line, text):
    return [p + '/' for p in glob.glob(expand_local_path(text) + '*') if
                                                               os.path.isdir(p)]

def do_chdir(command):
    """
    Usage: :chdir PATH
    Change the current directory of gsh (not the remote shells).
    """
    try:
        os.chdir(expand_local_path(command))
    except OSError, e:
        print e

def complete_send_ctrl(line, text):
    if len(line[:-1].split()) >= 2:
        # Control letter already given in command line
        return complete_shells(line, text, lambda i: i.enabled)
    if text in ('c', 'd', 'z'):
        return [text + ' ']
    return ['c ', 'd ', 'z ']

def do_send_ctrl(command):
    """
    Usage: :send_ctrl LETTER [SHELLS...]
    Send a control character to remote shells.
    The first argument is the control character to send: c, d or z.
    The remaining optional arguments are the destination shells.
    The special characters * ? and [] work as expected.
    """
    splitted = command.split()
    if not splitted:
        print 'Expected at least a letter'
        return
    letter = splitted[0]
    if len(letter) != 1:
        print 'Expected a single letter, got:', letter
        return
    control_letter = chr(ord(letter.lower()) - ord('a') + 1)
    for i in selected_shells(' '.join(splitted[1:])):
        if i.enabled:
            i.dispatch_write(control_letter)

def complete_reset_prompt(line, text):
    return complete_shells(line, text, lambda i: i.enabled)

def do_reset_prompt(command):
    """
    Usage: :reset_prompt [SHELLS...]
    Change the prompt to be recognized by gsh.
    The special characters * ? and [] work as expected.
    """
    for i in selected_shells(command):
        i.dispatch_write(i.init_string)

def complete_enable(line, text):
    return complete_shells(line, text, lambda i: i.active and not i.enabled)

def do_enable(command):
    """
    Usage: :enable [SHELLS...]
    Enable sending commands to remote shells.
    The special characters * ? and [] work as expected.
    """
    toggle_shells(command, True)

def complete_disable(line, text):
    return complete_shells(line, text, lambda i: i.enabled)

def do_disable(command):
    """
    Usage: :disable [SHELLS...]
    Disable sending commands to remote shells.
    The special characters * ? and [] work as expected.
    """
    toggle_shells(command, False)

def complete_reconnect(line, text):
    return complete_shells(line, text, lambda i: not i.active)

def do_reconnect(command):
    """
    Usage: :reconnect [SHELLS...]
    Try to reconnect to disconnected remote shells.
    The special characters * ? and [] work as expected.
    """
    for i in selected_shells(command):
        if not i.active:
            i.reconnect()

def do_add(command):
    """
    Usage: :add NAMES...
    Add one or many remote shells.
    """
    for host in command.split():
        remote_dispatcher.remote_dispatcher(host)

def complete_purge(line, text):
    return complete_shells(line, text, lambda i: not i.enabled)

def do_purge(command):
    """
    Usage: :purge [SHELLS...]
    Delete disabled remote shells.
    This helps to have a shorter list.
    The special characters * ? and [] work as expected.
    """
    to_delete = []
    for i in selected_shells(command):
        if not i.enabled:
            to_delete.append(i)
    for i in to_delete:
        i.disconnect()
        i.close()

def do_rename(command):
    """
    Usage: :rename [NEW_NAME]
    Rename all enabled remote shells with the argument.
    The argument will be shell expanded on the remote processes. With no
    argument, the original hostname will be restored as the displayed name.
    """
    for i in dispatchers.all_instances():
        if i.enabled:
            i.rename(command)

def do_hide_password(command):
    """
    Usage: :hide_password
    Do not echo the next typed line.
    This is useful when entering password. If debugging is enabled, it will be
    disabled to avoid displaying a password
    """
    warned = False
    for i in dispatchers.all_instances():
        if i.enabled and i.debug:
            i.debug = False
            if not warned:
                print 'Debugging disabled to avoid displaying passwords'
                warned = True
    stdin.set_echo(False)

def complete_set_debug(line, text):
    if len(line[:-1].split()) >= 2:
        # Debug value already given in command line
        return complete_shells(line, text)
    if text.lower() in ('y', 'n'):
        return [text + ' ']
    return ['y ', 'n ']

def do_set_debug(command):
    """
    Usage: :set_debug y|n [SHELLS...]
    Enable or disable debugging output for remote shells.
    The first argument is 'y' to enable the debugging output, 'n' to
    disable it.
    The remaining optional arguments are the selected shells.
    The special characters * ? and [] work as expected.
    """
    splitted = command.split()
    if not splitted:
        print 'Expected at least a letter'
        return
    letter = splitted[0].lower()
    if letter not in ('y', 'n'):
        print "Expected 'y' or 'n', got:", splitted[0]
        return
    debug = letter == 'y'
    for i in selected_shells(' '.join(splitted[1:])):
        i.debug = debug

def complete_replicate(line, text):
    if ':' not in text:
        return [c[:-1] + ':' for c in complete_shells(line, text)]
    shell, path = text.split(':')
    return [shell + ':' + p for p in complete_local_absolute_path(path)]

def do_replicate(command):
    """
    Usage: :replicate SHELL:path
    Copy a path from one remote shell to all others
    """
    if ':' not in command:
        print 'Usage: :replicate SHELL:path'
        return
    shell_name, path = command.split(':', 1)
    for shell in dispatchers.all_instances():
        if shell.display_name == shell_name:
            if not shell.enabled:
                print shell_name, 'is not enabled'
                return
            break
    else:
        print shell_name, 'not found'
        return
    file_transfer.replicate(shell, path)

def do_export_rank(command):
    """
    Usage: :export_rank
    Set GSH_RANK and GSH_NR_SHELLS on enabled remote shells.
    The GSH_RANK shell variable uniquely identifies each shell with a number
    between 0 and GSH_NR_SHELLS - 1. GSH_NR_SHELLS is the total number of
    enabled shells.
    """
    rank = 0
    for shell in dispatchers.all_instances():
        if shell.enabled:
            shell.dispatch_write('export GSH_RANK=%d\n' % rank)
            rank += 1

    for shell in dispatchers.all_instances():
        if shell.enabled:
            shell.dispatch_write('export GSH_NR_SHELLS=%d\n' % rank)

def main():
    """
    Output a help text of each control command suitable for the man page
    """
    for name in list_control_commands():
        print '<TP>'
        unstripped = get_control_command(name).__doc__.split('\n')
        lines = [l.strip() for l in unstripped]
        usage = lines[1].strip()
        print '<fB>%s<fR>' % usage[7:]
        help_text = ' '.join(lines[2:]).replace('gsh', '<fI>gsh<fR>').strip()
        print help_text

if __name__ == '__main__':
    main()

