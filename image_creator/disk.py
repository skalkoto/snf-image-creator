# -*- coding: utf-8 -*-
#
# Copyright (C) 2011-2017 GRNET S.A.
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

"""Module hosting the Disk class."""

import stat
import os
import tempfile
import uuid
import shutil

from image_creator.util import get_command, try_fail_repeat, free_space, \
    FatalError, create_snapshot, image_info
from image_creator.bundle_volume import BundleVolume
from image_creator.image import Image

dd = get_command('dd')
dmsetup = get_command('dmsetup')
losetup = get_command('losetup')
blockdev = get_command('blockdev')


def get_tmp_dir(default=None):
    """Check tmp directory candidates and return the one with the most
    available space.
    """
    if default is not None:
        return default

    # TODO: I need to find a better way of choosing temporary directories.
    # Maybe check all available mount points.
    TMP_CANDIDATES = [t for t in ('/var/tmp', os.path.expanduser('~'), '/mnt')
                      if os.access(t, os.W_OK)]

    space = [free_space(t) for t in TMP_CANDIDATES]

    max_idx = 0
    max_val = space[0]
    for i, val in zip(range(len(space)), space):
        if val > max_val:
            max_val = val
            max_idx = i

    # Return the candidate path with more available space
    return TMP_CANDIDATES[max_idx]


class Disk(object):
    """This class represents a hard disk hosting an Operating System

    A Disk instance never alters the source media it is created from.
    Any change is done on a snapshot created by the device-mapper of
    the Linux kernel.
    """

    def __init__(self, source, output, tmp=None):
        """Create a new Disk instance out of a source media. The source
        media can be an image file, a block device or a directory.
        """
        self._cleanup_jobs = []
        self._images = []
        self._file = None
        self.source = source
        self.out = output
        self.meta = {}
        self.tmp = tempfile.mkdtemp(prefix='.snf_image_creator.',
                                    dir=get_tmp_dir(tmp))

        self._add_cleanup(shutil.rmtree, self.tmp)

    def _add_cleanup(self, job, *args):
        """Add a new job in the cleanup list."""
        self._cleanup_jobs.append((job, args))

    def _losetup(self, fname):
        """Setup a loop device and add it to the cleanup list. The loop device
        will be detached when cleanup is called.
        """
        loop = losetup('-f', '--show', fname)
        loop = loop.strip()  # remove the new-line char
        self._add_cleanup(try_fail_repeat, losetup, '-d', loop)
        return loop

    def _dir_to_disk(self):
        """Create a disk out of a directory."""
        if self.source == '/':
            bundle = BundleVolume(self.out, self.meta)
            image = '%s/%s.raw' % (self.tmp, uuid.uuid4().hex)

            def check_unlink(path):
                """Unlinks file if exists"""
                if os.path.exists(path):
                    os.unlink(path)

            self._add_cleanup(check_unlink, image)
            bundle.create_image(image)
            return image
        raise FatalError("Using a directory as media source is supported")

    def cleanup(self):
        """Cleanup internal data. This needs to be called before the
        program ends.
        """
        try:
            while self._images:
                image = self._images.pop()
                image.destroy()
        finally:
            # Make sure those are executed even if one of the device.destroy
            # methods throws exeptions.
            while self._cleanup_jobs:
                job, args = self._cleanup_jobs.pop()
                job(*args)

    @property
    def file(self):
        """Convert the source media into a file."""

        if self._file is not None:
            return self._file

        self.out.info("Examining source media `%s' ..." % self.source, False)
        mode = os.stat(self.source).st_mode
        if stat.S_ISDIR(mode):
            self.out.success('looks like a directory')
            self._file = self._dir_to_disk()
        elif stat.S_ISREG(mode):
            self.out.success('looks like an image file')
            self._file = self.source
        elif not stat.S_ISBLK(mode):
            raise FatalError("Invalid media source. Only block devices, "
                             "regular files and directories are supported.")
        else:
            self.out.success('looks like a block device')
            self._file = self.source

        return self._file

    def snapshot(self):
        """Creates a snapshot of the original source media of the Disk
        instance.
        """

        if self.source == '/':
            self.out.warn("Snapshotting ignored for host bundling mode.")
            return self.file

        # Examine media file
        info = image_info(self.file)

        self.out.info("Snapshotting media source ...", False)

        # Create a qcow2 snapshot for image files that are not raw
        if info['format'] != 'raw':
            snapshot = create_snapshot(self.file, self.tmp)
            self._add_cleanup(os.unlink, snapshot)
            self.out.success('done')
            return snapshot

        # Create a device-mapper snapshot for raw image files and block devices
        mode = os.stat(self.file).st_mode
        device = self.file if stat.S_ISBLK(mode) else self._losetup(self.file)
        size = int(blockdev('--getsz', device))

        cowfd, cow = tempfile.mkstemp(dir=self.tmp)
        os.close(cowfd)
        self._add_cleanup(os.unlink, cow)
        # Create cow sparse file
        dd('if=/dev/null', 'of=%s' % cow, 'bs=512', 'seek=%d' % size)
        cowdev = self._losetup(cow)

        snapshot = 'snf-image-creator-snapshot-%s' % uuid.uuid4().hex
        tablefd, table = tempfile.mkstemp()
        try:
            try:
                os.write(tablefd, "0 %d snapshot %s %s n 8\n" %
                         (size, device, cowdev))
            finally:
                os.close(tablefd)

            dmsetup('create', snapshot, table)
            self._add_cleanup(try_fail_repeat, dmsetup, 'remove', snapshot)
        finally:
            os.unlink(table)
        self.out.success('done')
        return "/dev/mapper/%s" % snapshot

    def get_image(self, media, **kwargs):
        """Returns a newly created Image instance."""
        info = image_info(media)
        image = Image(media, self.out, format=info['format'], **kwargs)
        self._images.append(image)
        image.enable()
        return image

    def destroy_image(self, image):
        """Destroys an Image instance previously created with the get_image()
        method.
        """

        self._images.remove(image)
        image.destroy()

# vim: set sta sts=4 shiftwidth=4 sw=4 et ai :
