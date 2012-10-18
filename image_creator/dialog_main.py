#!/usr/bin/env python

# Copyright 2012 GRNET S.A. All rights reserved.
#
# Redistribution and use in source and binary forms, with or
# without modification, are permitted provided that the following
# conditions are met:
#
#   1. Redistributions of source code must retain the above
#      copyright notice, this list of conditions and the following
#      disclaimer.
#
#   2. Redistributions in binary form must reproduce the above
#      copyright notice, this list of conditions and the following
#      disclaimer in the documentation and/or other materials
#      provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY GRNET S.A. ``AS IS'' AND ANY EXPRESS
# OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL GRNET S.A OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF
# USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
# AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
# The views and conclusions contained in the software and
# documentation are those of the authors and should not be
# interpreted as representing official policies, either expressed
# or implied, of GRNET S.A.

import dialog
import sys
import os
import textwrap
import signal
import optparse

from image_creator import __version__ as version
from image_creator.util import FatalError
from image_creator.output import Output
from image_creator.output.cli import SimpleOutput
from image_creator.output.dialog import GaugeOutput
from image_creator.output.composite import CompositeOutput
from image_creator.disk import Disk
from image_creator.os_type import os_cls
from image_creator.dialog_wizard import wizard
from image_creator.dialog_menu import main_menu
from image_creator.dialog_util import SMALL_WIDTH, WIDTH, confirm_exit, \
    Reset, update_background_title


def image_creator(d, media, out):

    d.setBackgroundTitle('snf-image-creator')

    gauge = GaugeOutput(d, "Initialization", "Initializing...")
    out.add(gauge)
    disk = Disk(media, out)

    def signal_handler(signum, frame):
        gauge.cleanup()
        disk.cleanup()

    signal.signal(signal.SIGINT, signal_handler)
    try:
        snapshot = disk.snapshot()
        dev = disk.get_device(snapshot)

        metadata = {}
        for (key, value) in dev.meta.items():
            metadata[str(key)] = str(value)

        dev.mount(readonly=True)
        out.output("Collecting image metadata...")
        cls = os_cls(dev.distro, dev.ostype)
        image_os = cls(dev.root, dev.g, out)
        dev.umount()

        for (key, value) in image_os.meta.items():
            metadata[str(key)] = str(value)

        out.success("done")
        gauge.cleanup()
        out.remove(gauge)

        # Make sure the signal handler does not call gauge.cleanup again
        def dummy(self):
            pass
        gauge.cleanup = type(GaugeOutput.cleanup)(dummy, gauge, GaugeOutput)

        session = {"dialog": d,
                   "disk": disk,
                   "snapshot": snapshot,
                   "device": dev,
                   "image_os": image_os,
                   "metadata": metadata}

        msg = "snf-image-creator detected a %s system on the input media. " \
              "Would you like to run a wizard to assist you through the " \
              "image creation process?\n\nChoose <Wizard> to run the wizard," \
              " <Expert> to run the snf-image-creator in expert mode or " \
              "press ESC to quit the program." \
              % (dev.ostype if dev.ostype == dev.distro else "%s (%s)" %
                 (dev.ostype, dev.distro))

        update_background_title(session)

        while True:
            code = d.yesno(msg, width=WIDTH, height=12, yes_label="Wizard",
                           no_label="Expert")
            if code == d.DIALOG_OK:
                if wizard(session):
                    break
            elif code == d.DIALOG_CANCEL:
                main_menu(session)
                break

            if confirm_exit(d):
                break

        d.infobox("Thank you for using snf-image-creator. Bye", width=53)
    finally:
        disk.cleanup()

    return 0


def select_file(d, media):
    root = os.sep
    while 1:
        if media is not None:
            if not os.path.exists(media):
                d.msgbox("The file `%s' you choose does not exist." % media,
                         width=SMALL_WIDTH)
            else:
                break

        (code, media) = d.fselect(root, 10, 50,
                                  title="Please select input media")
        if code in (d.DIALOG_CANCEL, d.DIALOG_ESC):
            if confirm_exit(d, "You canceled the media selection dialog box."):
                sys.exit(0)
            else:
                media = None
                continue

    return media


def main():

    d = dialog.Dialog(dialog="dialog")

    # Add extra button in dialog library
    dialog._common_args_syntax["extra_button"] = \
        lambda enable: dialog._simple_option("--extra-button", enable)

    dialog._common_args_syntax["extra_label"] = \
        lambda string: ("--extra-label", string)

    # Allow yes-no label overwriting
    dialog._common_args_syntax["yes_label"] = \
        lambda string: ("--yes-label", string)

    dialog._common_args_syntax["no_label"] = \
        lambda string: ("--no-label", string)

    usage = "Usage: %prog [options] [<input_media>]"
    parser = optparse.OptionParser(version=version, usage=usage)
    parser.add_option("-l", "--logfile", type="string", dest="logfile",
                      default=None, help="log all messages to FILE",
                      metavar="FILE")

    options, args = parser.parse_args(sys.argv[1:])

    if len(args) > 1:
        parser.error("Wrong number of arguments")

    d.setBackgroundTitle('snf-image-creator')

    try:
        if os.geteuid() != 0:
            raise FatalError("You must run %s as root" %
                             parser.get_prog_name())

        media = select_file(d, args[0] if len(args) == 1 else None)

        logfile = None
        if options.logfile is not None:
            try:
                logfile = open(options.logfile, 'w')
            except IOError as e:
                raise FatalError(
                    "Unable to open logfile `%s' for writing. Reason: %s" %
                    (options.logfile, e.strerror))
        try:
            log = SimpleOutput(False, logfile) if logfile is not None \
                else Output()
            while 1:
                try:
                    out = CompositeOutput([log])
                    out.output("Starting %s v%s..." %
                               (parser.get_prog_name(), version))
                    ret = image_creator(d, media, out)
                    sys.exit(ret)
                except Reset:
                    log.output("Resetting everything...")
                    continue
        finally:
            if logfile is not None:
                logfile.close()
    except FatalError as e:
        msg = textwrap.fill(str(e), width=WIDTH)
        d.infobox(msg, width=WIDTH, title="Fatal Error")
        sys.exit(1)

# vim: set sta sts=4 shiftwidth=4 sw=4 et ai :