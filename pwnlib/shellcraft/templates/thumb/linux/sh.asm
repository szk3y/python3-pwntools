<% from pwnlib.shellcraft import thumb %>
<%docstring>
Execute a different process.

    >>> p = run_assembly(shellcraft.thumb.linux.sh())
    >>> p.sendline('echo Hello')
    >>> p.recv()
    b'Hello\n'

</%docstring>
${thumb.linux.execve('/bin///sh', 0, 0)}
