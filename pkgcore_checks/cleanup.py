# Copyright: 2006 Brian Harring <ferringb@gmail.com>
# License: GPL2

from pkgcore_checks.base import template, package_feed, Result


class RedundantVersionWarning(Result):
	description = "Redundant version of a pkg; keyword appears in a later version"

	__slots__ = ("category", "package", "later_versions")

	def __init__(self, pkg, higher_pkgs):
		self.category = pkg.category
		self.package = pkg.package
		self.version = pkg.fullver
		self.later_versions = tuple(x.fullver for x in higher_pkgs)
	
	def to_str(self, **kwds):
		return "%s/%s-%s: keywords are the same as version %r" % (self.category, self.package, self.version,
			", ".join(self.later_versions))

	def to_xml(self):
		return \
"""<check name="%s">
	<category>%s</category>
	<package>%s</package>
	<version>%s</version>
	<msg>keywords are the same as version(s): %s</msg>
</check>""" % (self.__class__.__name__, self.category, self.package, self.version, ", ".join(self.later_versions))


class RedundantVersionReport(template):
	"""scan for versions that are likely shadowed by later versions from a keywords standpoint
	Example: pkga-1 is keyworded amd64, pkga-2 is amd64.  
	pkga-2 can potentially be removed."""

	feed_type = package_feed
	
	def feed(self, pkgset, reporter):
		if len(pkgset) == 1:
			return

		stack = []
		for pkg in reversed(pkgset):
			matches = []
			curr_set = set(x for x in pkg.keywords if not x.startswith("-"))
			# reduce false positives for idiot keywords/ebuilds
			if not curr_set:
				continue
			for ver, keys in stack:
#				print pkg,ver,curr_set,curr_set.difference(keys)
				if not curr_set.difference(keys):
					matches.append(ver)
			stack.append([pkg, curr_set])
			if matches:
				reporter.add_report(RedundantVersionWarning(pkg, matches))
