from image_creator.os_type.linux import Linux


class Slackware(Linux):
    def cleanup_log(self):
        # In slackware the the installed packages info are stored in
        # /var/log/packages. Clearing all /var/log files will destroy
        # the package management
        self.foreach_file('/var/log', self.g.truncate, ftype='r', \
            exclude='/var/log/packages')

# vim: set sta sts=4 shiftwidth=4 sw=4 et ai :
