
from __future__ import (
    absolute_import,
)

import os.path, re, sys

from twisted.trial import unittest

from twisted.python import usage, runtime
from twisted.internet.defer import inlineCallbacks, returnValue

from allmydata.util import fileutil, pollmixin
from allmydata.util.encodingutil import unicode_to_argv, unicode_to_output, \
    get_filesystem_encoding
from allmydata.client import _Client
from allmydata.test import common_util
import allmydata
from allmydata import __appname__
from .common_util import parse_cli, run_cli

from ._twisted_9607 import (
    getProcessOutputAndValue,
)

timeout = 240

def get_root_from_file(src):
    srcdir = os.path.dirname(os.path.dirname(os.path.normcase(os.path.realpath(src))))

    root = os.path.dirname(srcdir)
    if os.path.basename(srcdir) == 'site-packages':
        if re.search(r'python.+\..+', os.path.basename(root)):
            root = os.path.dirname(root)
        root = os.path.dirname(root)
    elif os.path.basename(root) == 'src':
        root = os.path.dirname(root)

    return root

srcfile = allmydata.__file__
rootdir = get_root_from_file(srcfile)


class RunBinTahoeMixin:
    def skip_if_cannot_daemonize(self):
        if runtime.platformType == "win32":
            # twistd on windows doesn't daemonize. cygwin should work normally.
            raise unittest.SkipTest("twistd does not fork under windows")

    @inlineCallbacks
    def find_import_location(self):
        res = yield self.run_bintahoe(["--version-and-path"])
        out, err, rc_or_sig = res
        self.assertEqual(rc_or_sig, 0, res)
        lines = out.splitlines()
        tahoe_pieces = lines[0].split()
        self.assertEqual(tahoe_pieces[0], "%s:" % (__appname__,), (tahoe_pieces, res))
        returnValue(tahoe_pieces[-1].strip("()"))

    def run_bintahoe(self, args, stdin=None, python_options=[], env=None):
        command = sys.executable
        argv = python_options + ["-m", "allmydata.scripts.runner"] + args

        if env is None:
            env = os.environ

        d = getProcessOutputAndValue(command, argv, env, stdinBytes=stdin)
        def fix_signal(result):
            # Mirror subprocess.Popen.returncode structure
            (out, err, signal) = result
            return (out, err, -signal)
        d.addErrback(fix_signal)
        return d


class BinTahoe(common_util.SignalMixin, unittest.TestCase, RunBinTahoeMixin):
    @inlineCallbacks
    def test_the_right_code(self):
        # running "tahoe" in a subprocess should find the same code that
        # holds this test file, else something is weird
        test_path = os.path.dirname(os.path.dirname(os.path.normcase(os.path.realpath(srcfile))))
        bintahoe_import_path = yield self.find_import_location()

        same = (bintahoe_import_path == test_path)
        if not same:
            msg = ("My tests and my 'tahoe' executable are using different paths.\n"
                   "tahoe: %r\n"
                   "tests: %r\n"
                   "( according to the test source filename %r)\n" %
                   (bintahoe_import_path, test_path, srcfile))

            if (not isinstance(rootdir, unicode) and
                rootdir.decode(get_filesystem_encoding(), 'replace') != rootdir):
                msg += ("However, this may be a false alarm because the import path\n"
                        "is not representable in the filesystem encoding.")
                raise unittest.SkipTest(msg)
            else:
                msg += "Please run the tests in a virtualenv that includes both the Tahoe-LAFS library and the 'tahoe' executable."
                self.fail(msg)

    def test_path(self):
        d = self.run_bintahoe(["--version-and-path"])
        def _cb(res):
            from allmydata import normalized_version

            out, err, rc_or_sig = res
            self.failUnlessEqual(rc_or_sig, 0, str(res))

            # Fail unless the __appname__ package is *this* version *and*
            # was loaded from *this* source directory.

            required_verstr = str(allmydata.__version__)

            self.failIfEqual(required_verstr, "unknown",
                             "We don't know our version, because this distribution didn't come "
                             "with a _version.py and 'setup.py update_version' hasn't been run.")

            srcdir = os.path.dirname(os.path.dirname(os.path.normcase(os.path.realpath(srcfile))))
            info = repr((res, allmydata.__appname__, required_verstr, srcdir))

            appverpath = out.split(')')[0]
            (appverfull, path) = appverpath.split('] (')
            (appver, comment) = appverfull.split(' [')
            (branch, full_version) = comment.split(': ')
            (app, ver) = appver.split(': ')

            self.failUnlessEqual(app, allmydata.__appname__, info)
            norm_ver = normalized_version(ver)
            norm_required = normalized_version(required_verstr)
            self.failUnlessEqual(norm_ver, norm_required, info)
            self.failUnlessEqual(path, srcdir, info)
            self.failUnlessEqual(branch, allmydata.branch)
            self.failUnlessEqual(full_version, allmydata.full_version)
        d.addCallback(_cb)
        return d

    def test_unicode_arguments_and_output(self):
        tricky = u"\u2621"
        try:
            tricky_arg = unicode_to_argv(tricky, mangle=True)
            tricky_out = unicode_to_output(tricky)
        except UnicodeEncodeError:
            raise unittest.SkipTest("A non-ASCII argument/output could not be encoded on this platform.")

        d = self.run_bintahoe([tricky_arg])
        def _cb(res):
            out, err, rc_or_sig = res
            self.failUnlessEqual(rc_or_sig, 1, str(res))
            self.failUnlessIn("Unknown command: "+tricky_out, out)
        d.addCallback(_cb)
        return d

    def test_run_with_python_options(self):
        # -t is a harmless option that warns about tabs.
        d = self.run_bintahoe(["--version"], python_options=["-t"])
        def _cb(res):
            out, err, rc_or_sig = res
            self.failUnlessEqual(rc_or_sig, 0, str(res))
            self.failUnless(out.startswith(allmydata.__appname__+':'), str(res))
        d.addCallback(_cb)
        return d

    def test_version_no_noise(self):
        d = self.run_bintahoe(["--version"])
        def _cb(res):
            out, err, rc_or_sig = res
            self.failUnlessEqual(rc_or_sig, 0, str(res))
            self.failUnless(out.startswith(allmydata.__appname__+':'), str(res))
            self.failIfIn("DeprecationWarning", out, str(res))
            errlines = err.split("\n")
            self.failIf([True for line in errlines if (line != "" and "UserWarning: Unbuilt egg for setuptools" not in line
                                                                  and "from pkg_resources import load_entry_point" not in line)], str(res))
            if err != "":
                raise unittest.SkipTest("This test is known not to pass on Ubuntu Lucid; see #1235.")
        d.addCallback(_cb)
        return d

    @inlineCallbacks
    def test_help_eliot_destinations(self):
        out, err, rc_or_sig = yield self.run_bintahoe(["--help-eliot-destinations"])
        self.assertIn("\tfile:<path>", out)
        self.assertEqual(rc_or_sig, 0)

    @inlineCallbacks
    def test_eliot_destination(self):
        out, err, rc_or_sig = yield self.run_bintahoe([
            # Proves little but maybe more than nothing.
            "--eliot-destination=file:-",
            # Throw in *some* command or the process exits with error, making
            # it difficult for us to see if the previous arg was accepted or
            # not.
            "--help",
        ])
        self.assertEqual(rc_or_sig, 0)

    @inlineCallbacks
    def test_unknown_eliot_destination(self):
        out, err, rc_or_sig = yield self.run_bintahoe([
            "--eliot-destination=invalid:more",
        ])
        self.assertEqual(1, rc_or_sig)
        self.assertIn("Unknown destination description", out)
        self.assertIn("invalid:more", out)

    @inlineCallbacks
    def test_malformed_eliot_destination(self):
        out, err, rc_or_sig = yield self.run_bintahoe([
            "--eliot-destination=invalid",
        ])
        self.assertEqual(1, rc_or_sig)
        self.assertIn("must be formatted like", out)

    @inlineCallbacks
    def test_escape_in_eliot_destination(self):
        out, err, rc_or_sig = yield self.run_bintahoe([
            "--eliot-destination=file:@foo",
        ])
        self.assertEqual(1, rc_or_sig)
        self.assertIn("Unsupported escape character", out)


class CreateNode(unittest.TestCase):
    # exercise "tahoe create-node", create-introducer,
    # create-key-generator, and create-stats-gatherer, by calling the
    # corresponding code as a subroutine.

    def workdir(self, name):
        basedir = os.path.join("test_runner", "CreateNode", name)
        fileutil.make_dirs(basedir)
        return basedir

    @inlineCallbacks
    def do_create(self, kind, *args):
        basedir = self.workdir("test_" + kind)
        command = "create-" + kind
        is_client = kind in ("node", "client")
        tac = is_client and "tahoe-client.tac" or ("tahoe-" + kind + ".tac")

        n1 = os.path.join(basedir, command + "-n1")
        argv = ["--quiet", command, "--basedir", n1] + list(args)
        rc, out, err = yield run_cli(*argv)
        self.failUnlessEqual(err, "")
        self.failUnlessEqual(out, "")
        self.failUnlessEqual(rc, 0)
        self.failUnless(os.path.exists(n1))
        self.failUnless(os.path.exists(os.path.join(n1, tac)))

        if is_client:
            # tahoe.cfg should exist, and should have storage enabled for
            # 'create-node', and disabled for 'create-client'.
            tahoe_cfg = os.path.join(n1, "tahoe.cfg")
            self.failUnless(os.path.exists(tahoe_cfg))
            content = fileutil.read(tahoe_cfg).replace('\r\n', '\n')
            if kind == "client":
                self.failUnless(re.search(r"\n\[storage\]\n#.*\nenabled = false\n", content), content)
            else:
                self.failUnless(re.search(r"\n\[storage\]\n#.*\nenabled = true\n", content), content)
                self.failUnless("\nreserved_space = 1G\n" in content)

        # creating the node a second time should be rejected
        rc, out, err = yield run_cli(*argv)
        self.failIfEqual(rc, 0, str((out, err, rc)))
        self.failUnlessEqual(out, "")
        self.failUnless("is not empty." in err)

        # Fail if there is a non-empty line that doesn't end with a
        # punctuation mark.
        for line in err.splitlines():
            self.failIf(re.search("[\S][^\.!?]$", line), (line,))

        # test that the non --basedir form works too
        n2 = os.path.join(basedir, command + "-n2")
        argv = ["--quiet", command] + list(args) + [n2]
        rc, out, err = yield run_cli(*argv)
        self.failUnlessEqual(err, "")
        self.failUnlessEqual(out, "")
        self.failUnlessEqual(rc, 0)
        self.failUnless(os.path.exists(n2))
        self.failUnless(os.path.exists(os.path.join(n2, tac)))

        # test the --node-directory form
        n3 = os.path.join(basedir, command + "-n3")
        argv = ["--quiet", "--node-directory", n3, command] + list(args)
        rc, out, err = yield run_cli(*argv)
        self.failUnlessEqual(err, "")
        self.failUnlessEqual(out, "")
        self.failUnlessEqual(rc, 0)
        self.failUnless(os.path.exists(n3))
        self.failUnless(os.path.exists(os.path.join(n3, tac)))

        if kind in ("client", "node", "introducer"):
            # test that the output (without --quiet) includes the base directory
            n4 = os.path.join(basedir, command + "-n4")
            argv = [command] + list(args) + [n4]
            rc, out, err = yield run_cli(*argv)
            self.failUnlessEqual(err, "")
            self.failUnlessIn(" created in ", out)
            self.failUnlessIn(n4, out)
            self.failIfIn("\\\\?\\", out)
            self.failUnlessEqual(rc, 0)
            self.failUnless(os.path.exists(n4))
            self.failUnless(os.path.exists(os.path.join(n4, tac)))

        # make sure it rejects too many arguments
        self.failUnlessRaises(usage.UsageError, parse_cli,
                              command, "basedir", "extraarg")

        # when creating a non-client, there is no default for the basedir
        if not is_client:
            argv = [command]
            self.failUnlessRaises(usage.UsageError, parse_cli,
                                  command)

    def test_node(self):
        self.do_create("node", "--hostname=127.0.0.1")

    def test_client(self):
        # create-client should behave like create-node --no-storage.
        self.do_create("client")

    def test_introducer(self):
        self.do_create("introducer", "--hostname=127.0.0.1")

    def test_stats_gatherer(self):
        self.do_create("stats-gatherer", "--hostname=127.0.0.1")

    def test_subcommands(self):
        # no arguments should trigger a command listing, via UsageError
        self.failUnlessRaises(usage.UsageError, parse_cli,
                              )

    @inlineCallbacks
    def test_stats_gatherer_good_args(self):
        rc,out,err = yield run_cli("create-stats-gatherer", "--hostname=foo",
                                   self.mktemp())
        self.assertEqual(rc, 0)
        rc,out,err = yield run_cli("create-stats-gatherer",
                                   "--location=tcp:foo:1234",
                                   "--port=tcp:1234", self.mktemp())
        self.assertEqual(rc, 0)


    def test_stats_gatherer_bad_args(self):
        def _test(args):
            argv = args.split()
            self.assertRaises(usage.UsageError, parse_cli, *argv)

        # missing hostname/location/port
        _test("create-stats-gatherer D")

        # missing port
        _test("create-stats-gatherer --location=foo D")

        # missing location
        _test("create-stats-gatherer --port=foo D")

        # can't provide both
        _test("create-stats-gatherer --hostname=foo --port=foo D")

        # can't provide both
        _test("create-stats-gatherer --hostname=foo --location=foo D")

        # can't provide all three
        _test("create-stats-gatherer --hostname=foo --location=foo --port=foo D")

class RunNode(common_util.SignalMixin, unittest.TestCase, pollmixin.PollMixin,
              RunBinTahoeMixin):
    # exercise "tahoe start", for both introducer, client node, and
    # key-generator, by spawning "tahoe start" as a subprocess. This doesn't
    # get us figleaf-based line-level coverage, but it does a better job of
    # confirming that the user can actually run "./bin/tahoe start" and
    # expect it to work. This verifies that bin/tahoe sets up PYTHONPATH and
    # the like correctly.

    # This doesn't work on cygwin (it hangs forever), so we skip this test
    # when we're on cygwin. It is likely that "tahoe start" itself doesn't
    # work on cygwin: twisted seems unable to provide a version of
    # spawnProcess which really works there.

    def workdir(self, name):
        basedir = os.path.join("test_runner", "RunNode", name)
        fileutil.make_dirs(basedir)
        return basedir

    def test_introducer(self):
        self.skip_if_cannot_daemonize()

        basedir = self.workdir("test_introducer")
        c1 = os.path.join(basedir, "c1")
        exit_trigger_file = os.path.join(c1, _Client.EXIT_TRIGGER_FILE)
        twistd_pid_file = os.path.join(c1, "twistd.pid")
        introducer_furl_file = os.path.join(c1, "private", "introducer.furl")
        node_url_file = os.path.join(c1, "node.url")
        config_file = os.path.join(c1, "tahoe.cfg")

        d = self.run_bintahoe(["--quiet", "create-introducer", "--basedir", c1, "--hostname", "localhost"])
        def _cb(res):
            out, err, rc_or_sig = res
            self.failUnlessEqual(rc_or_sig, 0)

            # This makes sure that node.url is written, which allows us to
            # detect when the introducer restarts in _node_has_restarted below.
            config = fileutil.read(config_file)
            self.failUnlessIn('\nweb.port = \n', config)
            fileutil.write(config_file, config.replace('\nweb.port = \n', '\nweb.port = 0\n'))

            # by writing this file, we get ten seconds before the node will
            # exit. This insures that even if the test fails (and the 'stop'
            # command doesn't work), the client should still terminate.
            fileutil.write(exit_trigger_file, "")
            # now it's safe to start the node
        d.addCallback(_cb)

        def _then_start_the_node(res):
            return self.run_bintahoe(["--quiet", "start", c1])
        d.addCallback(_then_start_the_node)

        def _cb2(res):
            out, err, rc_or_sig = res

            fileutil.write(exit_trigger_file, "")
            errstr = "rc=%d, OUT: '%s', ERR: '%s'" % (rc_or_sig, out, err)
            self.failUnlessEqual(rc_or_sig, 0, errstr)
            self.failUnlessEqual(out, "", errstr)
            # self.failUnlessEqual(err, "", errstr) # See test_client_no_noise -- for now we ignore noise.

            # the parent (twistd) has exited. However, twistd writes the pid
            # from the child, not the parent, so we can't expect twistd.pid
            # to exist quite yet.

            # the node is running, but it might not have made it past the
            # first reactor turn yet, and if we kill it too early, it won't
            # remove the twistd.pid file. So wait until it does something
            # that we know it won't do until after the first turn.
        d.addCallback(_cb2)

        def _node_has_started():
            return os.path.exists(introducer_furl_file)
        d.addCallback(lambda res: self.poll(_node_has_started))

        def _started(res):
            # read the introducer.furl file so we can check that the contents
            # don't change on restart
            self.furl = fileutil.read(introducer_furl_file)

            fileutil.write(exit_trigger_file, "")
            self.failUnless(os.path.exists(twistd_pid_file))
            self.failUnless(os.path.exists(node_url_file))

            # rm this so we can detect when the second incarnation is ready
            os.unlink(node_url_file)
            return self.run_bintahoe(["--quiet", "restart", c1])
        d.addCallback(_started)

        def _then(res):
            out, err, rc_or_sig = res
            fileutil.write(exit_trigger_file, "")
            errstr = "rc=%d, OUT: '%s', ERR: '%s'" % (rc_or_sig, out, err)
            self.failUnlessEqual(rc_or_sig, 0, errstr)
            self.failUnlessEqual(out, "", errstr)
            # self.failUnlessEqual(err, "", errstr) # See test_client_no_noise -- for now we ignore noise.
        d.addCallback(_then)

        # Again, the second incarnation of the node might not be ready yet,
        # so poll until it is. This time introducer_furl_file already
        # exists, so we check for the existence of node_url_file instead.
        def _node_has_restarted():
            return os.path.exists(node_url_file)
        d.addCallback(lambda res: self.poll(_node_has_restarted))

        def _check_same_furl(res):
            self.failUnless(os.path.exists(introducer_furl_file))
            self.failUnlessEqual(self.furl, fileutil.read(introducer_furl_file))
        d.addCallback(_check_same_furl)

        # Now we can kill it. TODO: On a slow machine, the node might kill
        # itself before we get a chance to, especially if spawning the
        # 'tahoe stop' command takes a while.
        def _stop(res):
            fileutil.write(exit_trigger_file, "")
            self.failUnless(os.path.exists(twistd_pid_file))

            return self.run_bintahoe(["--quiet", "stop", c1])
        d.addCallback(_stop)

        def _after_stopping(res):
            out, err, rc_or_sig = res
            fileutil.write(exit_trigger_file, "")
            # the parent has exited by now
            errstr = "rc=%d, OUT: '%s', ERR: '%s'" % (rc_or_sig, out, err)
            self.failUnlessEqual(rc_or_sig, 0, errstr)
            self.failUnlessEqual(out, "", errstr)
            # self.failUnlessEqual(err, "", errstr) # See test_client_no_noise -- for now we ignore noise.
            # the parent was supposed to poll and wait until it sees
            # twistd.pid go away before it exits, so twistd.pid should be
            # gone by now.
            self.failIf(os.path.exists(twistd_pid_file))
        d.addCallback(_after_stopping)
        d.addBoth(self._remove, exit_trigger_file)
        return d
    # This test has hit a 240-second timeout on our feisty2.5 buildslave, and a 480-second timeout
    # on Francois's Lenny-armv5tel buildslave.
    test_introducer.timeout = 960

    def test_client_no_noise(self):
        self.skip_if_cannot_daemonize()

        basedir = self.workdir("test_client_no_noise")
        c1 = os.path.join(basedir, "c1")
        exit_trigger_file = os.path.join(c1, _Client.EXIT_TRIGGER_FILE)
        twistd_pid_file = os.path.join(c1, "twistd.pid")
        node_url_file = os.path.join(c1, "node.url")

        d = self.run_bintahoe(["--quiet", "create-client", "--basedir", c1, "--webport", "0"])
        def _cb(res):
            out, err, rc_or_sig = res
            errstr = "cc=%d, OUT: '%s', ERR: '%s'" % (rc_or_sig, out, err)
            assert rc_or_sig == 0, errstr
            self.failUnlessEqual(rc_or_sig, 0)

            # By writing this file, we get two minutes before the client will exit. This ensures
            # that even if the 'stop' command doesn't work (and the test fails), the client should
            # still terminate.
            fileutil.write(exit_trigger_file, "")
            # now it's safe to start the node
        d.addCallback(_cb)

        def _start(res):
            return self.run_bintahoe(["--quiet", "start", c1])
        d.addCallback(_start)

        def _cb2(res):
            out, err, rc_or_sig = res
            errstr = "cc=%d, OUT: '%s', ERR: '%s'" % (rc_or_sig, out, err)
            fileutil.write(exit_trigger_file, "")
            self.failUnlessEqual(rc_or_sig, 0, errstr)
            self.failUnlessEqual(out, "", errstr) # If you emit noise, you fail this test.
            errlines = err.split("\n")
            self.failIf([True for line in errlines if (line != "" and "UserWarning: Unbuilt egg for setuptools" not in line
                                                                  and "from pkg_resources import load_entry_point" not in line)], errstr)
            if err != "":
                raise unittest.SkipTest("This test is known not to pass on Ubuntu Lucid; see #1235.")

            # the parent (twistd) has exited. However, twistd writes the pid
            # from the child, not the parent, so we can't expect twistd.pid
            # to exist quite yet.

            # the node is running, but it might not have made it past the
            # first reactor turn yet, and if we kill it too early, it won't
            # remove the twistd.pid file. So wait until it does something
            # that we know it won't do until after the first turn.
        d.addCallback(_cb2)

        def _node_has_started():
            return os.path.exists(node_url_file)
        d.addCallback(lambda res: self.poll(_node_has_started))

        # now we can kill it. TODO: On a slow machine, the node might kill
        # itself before we get a chance to, especially if spawning the
        # 'tahoe stop' command takes a while.
        def _stop(res):
            self.failUnless(os.path.exists(twistd_pid_file),
                            (twistd_pid_file, os.listdir(os.path.dirname(twistd_pid_file))))
            return self.run_bintahoe(["--quiet", "stop", c1])
        d.addCallback(_stop)
        d.addBoth(self._remove, exit_trigger_file)
        return d

    def test_client(self):
        self.skip_if_cannot_daemonize()

        basedir = self.workdir("test_client")
        c1 = os.path.join(basedir, "c1")
        exit_trigger_file = os.path.join(c1, _Client.EXIT_TRIGGER_FILE)
        twistd_pid_file = os.path.join(c1, "twistd.pid")
        node_url_file = os.path.join(c1, "node.url")
        storage_furl_file = os.path.join(c1, "private", "storage.furl")
        config_file = os.path.join(c1, "tahoe.cfg")

        d = self.run_bintahoe(["--quiet", "create-node", "--basedir", c1,
                               "--webport", "0",
                               "--hostname", "localhost"])
        def _cb(res):
            out, err, rc_or_sig = res
            self.failUnlessEqual(rc_or_sig, 0)

            # Check that the --webport option worked.
            config = fileutil.read(config_file)
            self.failUnlessIn('\nweb.port = 0\n', config)

            # By writing this file, we get two minutes before the client will
            # exit. This ensures that even if the 'stop' command doesn't work
            # (and the test fails), the client should still terminate.
            fileutil.write(exit_trigger_file, "")
            # now it's safe to start the node
        d.addCallback(_cb)

        def _start(res):
            return self.run_bintahoe(["--quiet", "start", c1])
        d.addCallback(_start)

        def _cb2(res):
            out, err, rc_or_sig = res
            fileutil.write(exit_trigger_file, "")
            errstr = "rc=%d, OUT: '%s', ERR: '%s'" % (rc_or_sig, out, err)
            self.failUnlessEqual(rc_or_sig, 0, errstr)
            self.failUnlessEqual(out, "", errstr)
            # self.failUnlessEqual(err, "", errstr) # See test_client_no_noise -- for now we ignore noise.

            # the parent (twistd) has exited. However, twistd writes the pid
            # from the child, not the parent, so we can't expect twistd.pid
            # to exist quite yet.

            # the node is running, but it might not have made it past the
            # first reactor turn yet, and if we kill it too early, it won't
            # remove the twistd.pid file. So wait until it does something
            # that we know it won't do until after the first turn.
        d.addCallback(_cb2)

        def _node_has_started():
            return os.path.exists(node_url_file)
        d.addCallback(lambda res: self.poll(_node_has_started))

        def _started(res):
            # read the storage.furl file so we can check that its contents
            # don't change on restart
            self.storage_furl = fileutil.read(storage_furl_file)

            fileutil.write(exit_trigger_file, "")
            self.failUnless(os.path.exists(twistd_pid_file))

            # rm this so we can detect when the second incarnation is ready
            os.unlink(node_url_file)
            return self.run_bintahoe(["--quiet", "restart", c1])
        d.addCallback(_started)

        def _cb3(res):
            out, err, rc_or_sig = res

            fileutil.write(exit_trigger_file, "")
            errstr = "rc=%d, OUT: '%s', ERR: '%s'" % (rc_or_sig, out, err)
            self.failUnlessEqual(rc_or_sig, 0, errstr)
            self.failUnlessEqual(out, "", errstr)
            # self.failUnlessEqual(err, "", errstr) # See test_client_no_noise -- for now we ignore noise.
        d.addCallback(_cb3)

        # again, the second incarnation of the node might not be ready yet,
        # so poll until it is
        d.addCallback(lambda res: self.poll(_node_has_started))

        def _check_same_furl(res):
            self.failUnlessEqual(self.storage_furl,
                                 fileutil.read(storage_furl_file))
        d.addCallback(_check_same_furl)

        # now we can kill it. TODO: On a slow machine, the node might kill
        # itself before we get a chance to, especially if spawning the
        # 'tahoe stop' command takes a while.
        def _stop(res):
            fileutil.write(exit_trigger_file, "")
            self.failUnless(os.path.exists(twistd_pid_file),
                            (twistd_pid_file, os.listdir(os.path.dirname(twistd_pid_file))))
            return self.run_bintahoe(["--quiet", "stop", c1])
        d.addCallback(_stop)

        def _cb4(res):
            out, err, rc_or_sig = res

            fileutil.write(exit_trigger_file, "")
            # the parent has exited by now
            errstr = "rc=%d, OUT: '%s', ERR: '%s'" % (rc_or_sig, out, err)
            self.failUnlessEqual(rc_or_sig, 0, errstr)
            self.failUnlessEqual(out, "", errstr)
            # self.failUnlessEqual(err, "", errstr) # See test_client_no_noise -- for now we ignore noise.
            # the parent was supposed to poll and wait until it sees
            # twistd.pid go away before it exits, so twistd.pid should be
            # gone by now.
            self.failIf(os.path.exists(twistd_pid_file))
        d.addCallback(_cb4)
        d.addBoth(self._remove, exit_trigger_file)
        return d

    def _remove(self, res, file):
        fileutil.remove(file)
        return res

    def test_baddir(self):
        self.skip_if_cannot_daemonize()
        basedir = self.workdir("test_baddir")
        fileutil.make_dirs(basedir)

        d = self.run_bintahoe(["--quiet", "start", "--basedir", basedir])
        def _cb(res):
            out, err, rc_or_sig = res
            self.failUnlessEqual(rc_or_sig, 1)
            self.failUnless("is not a recognizable node directory" in err, err)
        d.addCallback(_cb)

        def _then_stop_it(res):
            return self.run_bintahoe(["--quiet", "stop", "--basedir", basedir])
        d.addCallback(_then_stop_it)

        def _cb2(res):
            out, err, rc_or_sig = res
            self.failUnlessEqual(rc_or_sig, 2)
            self.failUnless("does not look like a running node directory" in err)
        d.addCallback(_cb2)

        def _then_start_in_bogus_basedir(res):
            not_a_dir = os.path.join(basedir, "bogus")
            return self.run_bintahoe(["--quiet", "start", "--basedir", not_a_dir])
        d.addCallback(_then_start_in_bogus_basedir)

        def _cb3(res):
            out, err, rc_or_sig = res
            self.failUnlessEqual(rc_or_sig, 1)
            self.failUnlessIn("does not look like a directory at all", err)
        d.addCallback(_cb3)
        return d
