"""Cross-process exclusive locks; the lock file must stay separate from state."""
import errno
import os
import time


def acquire(stream, *, blocking=True):
    if os.name != 'nt':
        import fcntl
        fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return
    import msvcrt
    # Windows locks byte ranges. Never truncate a file another process locks.
    stream.seek(0, os.SEEK_END)
    if stream.tell() == 0:
        stream.write(b'\0')
        stream.flush()
    while True:
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            if not blocking:
                raise BlockingIOError(errno.EAGAIN, 'another process owns this lock') from None
            time.sleep(.05)


def release(stream):
    if os.name == 'nt':
        import msvcrt
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream, fcntl.LOCK_UN)
