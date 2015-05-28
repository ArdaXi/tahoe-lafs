
# Windows near-equivalent to twisted.internet.inotify
# This should only be imported on Windows.

import os, sys

from twisted.internet import reactor
from twisted.internet.threads import deferToThread

from allmydata.util.fake_inotify import humanReadableMask, \
    IN_WATCH_MASK, IN_ACCESS, IN_MODIFY, IN_ATTRIB, IN_CLOSE_NOWRITE, IN_CLOSE_WRITE, \
    IN_OPEN, IN_MOVED_FROM, IN_MOVED_TO, IN_CREATE, IN_DELETE, IN_DELETE_SELF, \
    IN_MOVE_SELF, IN_UNMOUNT, IN_Q_OVERFLOW, IN_IGNORED, IN_ONLYDIR, IN_DONT_FOLLOW, \
    IN_MASK_ADD, IN_ISDIR, IN_ONESHOT, IN_CLOSE, IN_MOVED, IN_CHANGED
[humanReadableMask, \
    IN_WATCH_MASK, IN_ACCESS, IN_MODIFY, IN_ATTRIB, IN_CLOSE_NOWRITE, IN_CLOSE_WRITE, \
    IN_OPEN, IN_MOVED_FROM, IN_MOVED_TO, IN_CREATE, IN_DELETE, IN_DELETE_SELF, \
    IN_MOVE_SELF, IN_UNMOUNT, IN_Q_OVERFLOW, IN_IGNORED, IN_ONLYDIR, IN_DONT_FOLLOW, \
    IN_MASK_ADD, IN_ISDIR, IN_ONESHOT, IN_CLOSE, IN_MOVED, IN_CHANGED]

from allmydata.util.assertutil import _assert, precondition
from allmydata.util.encodingutil import quote_output
from allmydata.util import log, fileutil
from allmydata.util.pollmixin import PollMixin

from ctypes import WINFUNCTYPE, WinError, windll, POINTER, byref, \
    create_string_buffer, addressof, Structure
from ctypes.wintypes import BOOL, HANDLE, DWORD, LPCWSTR, LPVOID

# <https://msdn.microsoft.com/en-us/library/windows/desktop/gg258116%28v=vs.85%29.aspx>
FILE_LIST_DIRECTORY              = 1

# <https://msdn.microsoft.com/en-us/library/windows/desktop/aa363858%28v=vs.85%29.aspx>
CreateFileW = WINFUNCTYPE(HANDLE, LPCWSTR, DWORD, DWORD, LPVOID, DWORD, DWORD, HANDLE) \
                  (("CreateFileW", windll.kernel32))

FILE_SHARE_READ                  = 0x00000001
FILE_SHARE_WRITE                 = 0x00000002
FILE_SHARE_DELETE                = 0x00000004

OPEN_EXISTING                    = 3

FILE_FLAG_BACKUP_SEMANTICS       = 0x02000000
FILE_FLAG_OVERLAPPED             = 0x40000000

# <https://msdn.microsoft.com/en-us/library/windows/desktop/ms724211%28v=vs.85%29.aspx>
CloseHandle = WINFUNCTYPE(BOOL, HANDLE)(("CloseHandle", windll.kernel32))

# <https://msdn.microsoft.com/en-us/library/windows/desktop/ms684342%28v=vs.85%29.aspx>
class OVERLAPPED(Structure):
    _fields_ = [('Internal', LPVOID),
                ('InternalHigh', LPVOID),
                ('Offset', DWORD),
                ('OffsetHigh', DWORD),
                ('Pointer', LPVOID),
                ('hEvent', HANDLE),
               ]

# <https://msdn.microsoft.com/en-us/library/windows/desktop/aa365465%28v=vs.85%29.aspx>
ReadDirectoryChangesW = WINFUNCTYPE(BOOL, HANDLE, LPVOID, DWORD, BOOL, DWORD,
                                    POINTER(DWORD), POINTER(OVERLAPPED), LPVOID) \
                            (("ReadDirectoryChangesW", windll.kernel32))

FILE_NOTIFY_CHANGE_FILE_NAME     = 0x00000001
FILE_NOTIFY_CHANGE_DIR_NAME      = 0x00000002
FILE_NOTIFY_CHANGE_ATTRIBUTES    = 0x00000004
#FILE_NOTIFY_CHANGE_SIZE         = 0x00000008
FILE_NOTIFY_CHANGE_LAST_WRITE    = 0x00000010
FILE_NOTIFY_CHANGE_LAST_ACCESS   = 0x00000020
#FILE_NOTIFY_CHANGE_CREATION     = 0x00000040
FILE_NOTIFY_CHANGE_SECURITY      = 0x00000100

# <https://msdn.microsoft.com/en-us/library/windows/desktop/aa364391%28v=vs.85%29.aspx>
FILE_ACTION_ADDED                = 0x00000001
FILE_ACTION_REMOVED              = 0x00000002
FILE_ACTION_MODIFIED             = 0x00000003
FILE_ACTION_RENAMED_OLD_NAME     = 0x00000004
FILE_ACTION_RENAMED_NEW_NAME     = 0x00000005

_action_to_string = {
    FILE_ACTION_ADDED            : "FILE_ACTION_ADDED",
    FILE_ACTION_REMOVED          : "FILE_ACTION_REMOVED",
    FILE_ACTION_MODIFIED         : "FILE_ACTION_MODIFIED",
    FILE_ACTION_RENAMED_OLD_NAME : "FILE_ACTION_RENAMED_OLD_NAME",
    FILE_ACTION_RENAMED_NEW_NAME : "FILE_ACTION_RENAMED_NEW_NAME",
}

_action_to_inotify_mask = {
    FILE_ACTION_ADDED            : IN_CREATE,
    FILE_ACTION_REMOVED          : IN_DELETE,
    FILE_ACTION_MODIFIED         : IN_CHANGED,
    FILE_ACTION_RENAMED_OLD_NAME : IN_MOVED_FROM,
    FILE_ACTION_RENAMED_NEW_NAME : IN_MOVED_TO,
}

INVALID_HANDLE_VALUE             = 0xFFFFFFFF

# <https://msdn.microsoft.com/en-us/library/windows/desktop/ms687025%28v=vs.85%29.aspx>
WaitForMultipleObjects = WINFUNCTYPE(DWORD, DWORD, POINTER(HANDLE), BOOL, DWORD) \
                             (("WaitForMultipleObjects", windll.kernel32))

INFINITE           = 0xFFFFFFFF

WAIT_ABANDONED     = 0x00000080
WAIT_IO_COMPLETION = 0x000000C0
WAIT_OBJECT_0      = 0x00000000
WAIT_TIMEOUT       = 0x00000102
WAIT_FAILED        = 0xFFFFFFFF

# <https://msdn.microsoft.com/en-us/library/windows/desktop/ms682396%28v=vs.85%29.aspx>
CreateEventW = WINFUNCTYPE(HANDLE, LPVOID, BOOL, BOOL, LPCWSTR) \
                   (("CreateEventW", windll.kernel32))

# <https://msdn.microsoft.com/en-us/library/windows/desktop/ms686211%28v=vs.85%29.aspx>
SetEvent = WINFUNCTYPE(BOOL, HANDLE)(("SetEvent", windll.kernel32))

FALSE = 0
TRUE  = 1

def _create_event(auto_reset):
    # no security descriptor, auto_reset, unsignalled, anonymous
    hEvent = CreateEventW(None, auto_reset, FALSE, None)
    if hEvent is None:
        raise WinError()

def _signal_event(hEvent):
    if SetEvent(hEvent) == 0:
        raise WinError()


class StoppedException(Exception):
    """The notifier has been stopped."""
    pass


class Notification(object):
    """
    * action:   a FILE_ACTION_* constant (not a bit mask)
    * filename: a Unicode string, giving the name relative to the watched directory
    """
    def __init__(self, action, filename):
        self.action = action
        self.filename = filename

    def __repr__(self):
        return "Notification(%r, %r)" % (_action_to_string.get(self.action, self.action), self.filename)


class FileNotifyInformation(object):
    """
    I represent a buffer containing FILE_NOTIFY_INFORMATION structures, and can
    iterate over those structures, decoding them into Notification objects.
    """

    def __init__(self, size=1024):
        self._size = size
        self._buffer = create_string_buffer(size)
        address = addressof(self._buffer)
        _assert(address & 3 == 0, "address 0x%X returned by create_string_buffer is not DWORD-aligned" % (address,))

        self._hStopped  = _create_event(auto_reset=FALSE)
        self._hNotified = _create_event(auto_reset=TRUE)
        self._events = (HANDLE*2)(self._hStopped, self._hNotified)

        self._overlapped = OVERLAPPED()
        self._overlapped.Internal = None
        self._overlapped.InternalHigh = None
        self._overlapped.Offset = 0
        self._overlapped.OffsetHigh = 0
        self._overlapped.Pointer = None
        self._overlapped.hEvent = self._hNotified

    def __del__(self):
        if hasattr(self, '_hStopped'):
            CloseHandle(self._hStopped)
        if hasattr(self, '_hNotified'):
            CloseHandle(self._hNotified)

    def stop(self):
        _signal_event(self._hStopped)

    def read_notifications(self, hDirectory, recursive, filter):
        """This does not block."""

        bytes_returned = DWORD(0)
        print "here"
        r = ReadDirectoryChangesW(hDirectory,
                                  self._buffer,
                                  self._size,
                                  recursive,
                                  filter,
                                  byref(bytes_returned),
                                  self._overlapped,
                                  None   # NULL -> no completion routine
                                 )
        if r == 0:
            raise WinError()

    def get_notifications(self):
        """This blocks and then iterates over the notifications."""

        r = WaitForMultipleObjects(2, self._events,
                                   FALSE, # wait for any, not all
                                   INFINITE)
        if r == WAIT_FAILED:
            raise WinError()
        if r == WAIT_OBJECT_0:  # hStopped
            raise StoppedException()
        if r != WAIT_OBJECT_0+1:  # hNotified
            raise OSError("unexpected return from WaitForMultipleObjects: %d" % (r,))

        data = self._buffer.raw[:bytes_returned.value]
        print data

        pos = 0
        while True:
            bytes = _read_dword(data, pos+8)
            s = Notification(_read_dword(data, pos+4),
                             data[pos+12 : pos+12+bytes].decode('utf-16-le'))

            next_entry_offset = _read_dword(data, pos)
            print s
            yield s
            if next_entry_offset == 0:
                break
            pos = pos + next_entry_offset


def _read_dword(data, i):
    # little-endian
    return ( ord(data[i])          |
            (ord(data[i+1]) <<  8) |
            (ord(data[i+2]) << 16) |
            (ord(data[i+3]) << 24))


def _open_directory(path_u):
    hDirectory = CreateFileW(path_u,
                             FILE_LIST_DIRECTORY,         # access rights
                             FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                                                          # don't prevent other processes from accessing
                             None,                        # no security descriptor
                             OPEN_EXISTING,               # directory must already exist
                             FILE_FLAG_BACKUP_SEMANTICS,  # necessary to open a directory
                             None                         # no template file
                            )
    if hDirectory == INVALID_HANDLE_VALUE:
        e = WinError()
        raise OSError("Opening directory %s gave Windows error %r: %s" % (quote_output(path_u), e.args[0], e.args[1]))
    return hDirectory


def simple_test():
    path_u = u"test"
    filter = FILE_NOTIFY_CHANGE_FILE_NAME | FILE_NOTIFY_CHANGE_DIR_NAME | FILE_NOTIFY_CHANGE_LAST_WRITE
    recursive = False

    hDirectory = _open_directory(path_u)
    fni = FileNotifyInformation()
    print "Waiting..."
    while True:
        fni.read_notifications(hDirectory, recursive, filter)
        for info in fni.get_notifications():
            print info


class INotify(PollMixin):
    def __init__(self):
        self._fni = FileNotifyInformation()
        self._started = False
        self._stop = False
        self._stopped = False
        self._filter = None
        self._callbacks = None
        self._hDirectory = None
        self._path = None
        self._pending = set()
        self._pending_delay = 1.0

    def set_pending_delay(self, delay):
        self._pending_delay = delay

    def startReading(self):
        # Twisted's version of this is synchronous.
        deferToThread(self._thread)
        return self.poll(lambda: self._started)

    def stopReading(self):
        self._stop = True
        self._fni.stop()

    def wait_until_stopped(self):
        if not self._stop:
            self.stopReading()
        return self.poll(lambda: self._stopped)

    def watch(self, path, mask=IN_WATCH_MASK, autoAdd=False, callbacks=None, recursive=False):
        precondition(not self._started, "watch() can only be called before startReading()")
        precondition(self._filter is None, "only one watch is supported")
        precondition(isinstance(autoAdd, bool), autoAdd=autoAdd)
        precondition(isinstance(recursive, bool), recursive=recursive)
        precondition(autoAdd == recursive, "need autoAdd and recursive to be the same", autoAdd=autoAdd, recursive=recursive)

        self._path = path
        path_u = path.path
        if not isinstance(path_u, unicode):
            path_u = path_u.decode(sys.getfilesystemencoding())
            _assert(isinstance(path_u, unicode), path_u=path_u)

        self._filter = FILE_NOTIFY_CHANGE_FILE_NAME | FILE_NOTIFY_CHANGE_DIR_NAME | FILE_NOTIFY_CHANGE_LAST_WRITE

        if mask & (IN_ACCESS | IN_CLOSE_NOWRITE | IN_OPEN):
            self._filter = self._filter | FILE_NOTIFY_CHANGE_LAST_ACCESS
        if mask & IN_ATTRIB:
            self._filter = self._filter | FILE_NOTIFY_CHANGE_ATTRIBUTES | FILE_NOTIFY_CHANGE_SECURITY

        self._recursive = recursive
        self._callbacks = callbacks or []
        self._hDirectory = _open_directory(path_u)

    def _thread(self):
        try:
            _assert(self._filter is not None, "no watch set")

            # To call Twisted or Tahoe APIs, use reactor.callFromThread as described in
            # <http://twistedmatrix.com/documents/current/core/howto/threading.html>.

            while True:
                # We must set _started to True *after* calling read_notifications, so that
                # the caller of startReading() can tell when we've actually started reading.

                self._fni.read_notifications(self._hDirectory, self._recursive, self._filter)
                self._started = True

                for info in self._fni.get_notifications():
                    print info
                    if self._stop:
                        raise StoppedException()

                    path = self._path.preauthChild(info.filename)  # FilePath with Unicode path
                    #mask = _action_to_inotify_mask.get(info.action, IN_CHANGED)

                    def _maybe_notify(path):
                        if path not in self._pending:
                            self._pending.add(path)
                            def _do_callbacks():
                                self._pending.remove(path)
                                for cb in self._callbacks:
                                    try:
                                        cb(None, path, IN_CHANGED)
                                    except Exception, e:
                                        log.err(e)
                            reactor.callLater(self._pending_delay, _do_callbacks)
                    reactor.callFromThread(_maybe_notify, path)
        except StoppedException:
            self._do_stop()
        except Exception, e:
            log.err(e)
            self._do_stop()
            raise

    def _do_stop(self):
        hDirectory = self._hDirectory
        self._callbacks = []
        self._hDirectory = None
        CloseHandle(hDirectory)
        self._stopped = True