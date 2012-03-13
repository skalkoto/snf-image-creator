#!/usr/bin/env python

from image_creator.util import get_command
from clint.textui import progress

import stat
import os
import tempfile
import uuid
import re
import sys
import guestfs


class DiskError(Exception):
    pass

dd = get_command('dd')
dmsetup = get_command('dmsetup')
losetup = get_command('losetup')
blockdev = get_command('blockdev')


class Disk(object):
    """This class represents a hard disk hosting an Operating System

    A Disk instance never alters the source media it is created from.
    Any change is done on a snapshot created by the device-mapper of
    the Linux kernel.
    """

    def __init__(self, source):
        """Create a new Disk instance out of a source media. The source
        media can be an image file, a block device or a directory."""
        self._cleanup_jobs = []
        self._devices = []
        self.source = source

    def _add_cleanup(self, job, *args):
        self._cleanup_jobs.append((job, args))

    def _losetup(self, fname):
        loop = losetup('-f', '--show', fname)
        loop = loop.strip() # remove the new-line char
        self._add_cleanup(losetup, '-d', loop)
        return loop

    def _dir_to_disk(self):
        raise NotImplementedError

    def cleanup(self):
        """Cleanup internal data. This needs to be called before the
        program ends.
        """
        while len(self._devices):
            device = self._devices.pop()
            device.destroy()

        while len(self._cleanup_jobs):
            job, args = self._cleanup_jobs.pop()
            job(*args)

    def get_device(self):
        """Returns a newly created DiskDevice instance.

        This instance is a snapshot of the original source media of
        the Disk instance.
        """
        sourcedev = self.source
        mode = os.stat(self.source).st_mode
        if stat.S_ISDIR(mode):
            return self._losetup(self._dir_to_disk())
        elif stat.S_ISREG(mode):
            sourcedev = self._losetup(self.source)
        elif not stat.S_ISBLK(mode):
            raise ValueError("Value for self.source is invalid")

        # Take a snapshot and return it to the user
        size = blockdev('--getsize', sourcedev)
        cowfd, cow = tempfile.mkstemp()
        self._add_cleanup(os.unlink, cow)
        # Create 1G cow sparse file
        dd('if=/dev/null', 'of=%s' % cow, 'bs=1k', 'seek=%d' % (1024 * 1024))
        cowdev = self._losetup(cow)

        snapshot = uuid.uuid4().hex
        tablefd, table = tempfile.mkstemp()
        try:
            os.write(tablefd, "0 %d snapshot %s %s n 8" % \
                                        (int(size), sourcedev, cowdev))
            dmsetup('create', snapshot, table)
            self._add_cleanup(dmsetup, 'remove', snapshot)
        finally:
            os.unlink(table)

        new_device = DiskDevice("/dev/mapper/%s" % snapshot)
        self._devices.append(new_device)
        return new_device

    def destroy_device(self, device):
        """Destroys a DiskDevice instance previously created by
        get_device method.
        """
        self._devices.remove(device)
        device.destroy()


def progress_generator(total):
    position = 0;
    for i in progress.bar(range(total)):
        if i < position:
            continue
        position = yield
    yield #suppress the StopIteration exception


class DiskDevice(object):
    """This class represents a block device hosting an Operating System
    as created by the device-mapper.
    """

    def __init__(self, device, bootable=True):
        """Create a new DiskDevice."""
        self.device = device
        self.bootable = bootable
        self.progress_bar = None

        self.g = guestfs.GuestFS()
        self.g.add_drive_opts(device, readonly=0)

        #self.g.set_trace(1)
        #self.g.set_verbose(1)

        eh = self.g.set_event_callback(self.progress_callback, guestfs.EVENT_PROGRESS)
        self.g.launch()
        self.g.delete_event_callback(eh)
        
        roots = self.g.inspect_os()
        if len(roots) == 0:
            raise DiskError("No operating system found")
        if len(roots) > 1:
            raise DiskError("Multiple operating systems found")

        self.root = roots[0]
        self.ostype = self.g.inspect_get_type(self.root)
        self.distro = self.g.inspect_get_distro(self.root)

    def destroy(self):
        """Destroy this DiskDevice instance."""
        self.g.umount_all()
        self.g.sync()
        # Close the guestfs handler
        self.g.close()

    def progress_callback(self, ev, eh, buf, array):
        position = array[2]
        total = array[3]
        
        if self.progress_bar is None:
            self.progress_bar = progress_generator(total)
            self.progress_bar.next()

        self.progress_bar.send(position)

        if position == total:
            self.progress_bar = None

    def mount(self):
        """Mount all disk partitions in a correct order."""
        mps = self.g.inspect_get_mountpoints(self.root)

        # Sort the keys to mount the fs in a correct order.
        # / should be mounted befor /boot, etc
        def compare(a, b):
            if len(a[0]) > len(b[0]):
                return 1
            elif len(a[0]) == len(b[0]):
                return 0
            else:
                return -1
        mps.sort(compare)
        for mp, dev in mps:
            try:
                self.g.mount(dev, mp)
            except RuntimeError as msg:
                print "%s (ignored)" % msg

    def umount(self):
        """Umount all mounted filesystems."""
        self.g.umount_all()

    def shrink(self):
        """Shrink the disk.

        This is accomplished by shrinking the last filesystem in the
        disk and then updating the partition table. The new disk size
        (in bytes) is returned.
        """
        dev = self.g.part_to_dev(self.root)
        parttype = self.g.part_get_parttype(dev)
        if parttype != 'msdos':
            raise DiskError("You have a %s partition table. "
                "Only msdos partitions are supported" % parttype)

        last_partition = self.g.part_list(dev)[-1]

        if last_partition['part_num'] > 4:
            raise DiskError("This disk contains logical partitions. "
                "Only primary partitions are supported.")

        part_dev = "%s%d" % (dev, last_partition['part_num'])
        fs_type = self.g.vfs_type(part_dev)
        if not re.match("ext[234]", fs_type):
            print "Warning: Don't know how to resize %s partitions." % vfs_type
            return

        self.g.e2fsck_f(part_dev)
        self.g.resize2fs_M(part_dev)
        output = self.g.tune2fs_l(part_dev)
        block_size = int(filter(lambda x: x[0] == 'Block size', output)[0][1])
        block_cnt = int(filter(lambda x: x[0] == 'Block count', output)[0][1])

        sector_size = self.g.blockdev_getss(dev)

        start = last_partition['part_start'] / sector_size
        end = start + (block_size * block_cnt) / sector_size - 1

        self.g.part_del(dev, last_partition['part_num'])
        self.g.part_add(dev, 'p', start, end)

        return (end + 1) * sector_size

    def size(self):
        """Returns the "payload" size of the device.

        The size returned by this method is the size of the space occupied by
        the partitions (including the space before the first partition).
        """
        dev = self.g.part_to_dev(self.root)
        last = self.g.part_list(dev)[-1]

        return last['part_end']

# vim: set sta sts=4 shiftwidth=4 sw=4 et ai :