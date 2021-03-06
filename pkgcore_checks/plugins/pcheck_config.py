# Copyright: 2006 Marien Zwart <marienz@gentoo.org>
# License: BSD/GPL2


"""Extra default config sections from pkgcore-checks."""


from pkgcore.config import basics


pkgcore_plugins = {
    'global_config': [{
            'no-arch-checks': basics.ConfigSectionFromStringDict({
                    'class': 'pkgcore_checks.base.Blacklist',
                    'patterns': 'unstable_only stale_unstable imlate',
                    }),
            'all-checks': basics.ConfigSectionFromStringDict({
                    'class': 'pkgcore_checks.base.Blacklist',
                    'patterns': '',
                    }),
            }],
    }
