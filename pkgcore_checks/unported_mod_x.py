# Copyright: 2006 Brian Harring <ferringb@gmail.com>
# License: GPL2

from pkgcore.util.compatibility import any
from pkgcore.util.demandload import demandload
from pkgcore_checks import base, util, arches
from pkgcore.util.iterables import caching_iter
from pkgcore.util.lists import stable_unique, iflatten_instance, unstable_unique
from pkgcore.util.containers import ProtectedSet
from pkgcore.restrictions import packages, values
from pkgcore.package.atom import atom
from pkgcore.package import virtual
demandload(globals(), "pkgcore.util.containers:InvertedContains")
demandload(globals(), "pkgcore.util.xml:escape")
demandload(globals(), "urllib:urlopen")

class ModularXPortingReport(base.template):

	"""modular X porting report.
	Scans for dependencies that require monolithic X, or via visibility limiters from profiles, are forced to use monolithic X
	"""
	feed_type = base.package_feed
	requires_profiles = True
	uses_caches = True

	valid_modx_pkgs_url = "http://www.gentoo.org/proj/en/desktop/x/x11/modular-x-packages.txt"

	def __init__(self, arches=arches.default_arches):
		self.arches = frozenset(x.lstrip("~") for x in arches)
		self.repo = self.profile_filters = None
		self.keywords_filter = None
		# use 7.1 so it catches any >7.0
		self.x7 = virtual.package("virtual/x11-7.1", None)
		self.x6 = virtual.package("virtual/x11-6.9", None)
		self.valid_modx_keys = frozenset(x for x in (y.strip() for y in urlopen(self.valid_modx_pkgs_url)) if x and x != "virtual/x11")
	
	def start(self, repo, global_insoluable, keywords_filter, profile_filters):
		self.repo = repo
		self.global_insoluable = global_insoluable
		self.keywords_filter = keywords_filter
		self.profile_filters = profile_filters
		
	def feed(self, pkgset, reporter, feeder):
		query_cache = {}
		# query_cache gets caching_iter partial repo searches shoved into it- reason is simple,
		# it's likely that versions of this pkg probably use similar deps- so we're forcing those
		# packages that were accessed for atom matching to remain in memory.
		# end result is less going to disk
		unported = []
		ported = []
		for pkg in pkgset:
			self.check_pkg(pkg, feeder, reporter, unported, ported)

		if unported and ported:
			for u in unported:
				reporter.add_report(SuggestRemoval(u, ported))

	def invalid_virtual_x11(self, atom):
		return atom.match(self.x7) and not atom.match(self.x6)

	def check_pkg(self, pkg, feeder, reporter, unported, ported):
		query_cache = feeder.query_cache
		failed = []
		
		ported_status = False
		for attr, depset in (("depends", pkg.depends), ("rdepends/pdepends", pkg.rdepends)):
			bad = set()
			for a in iflatten_instance(depset, atom):
				if not a.key == "virtual/x11" or a.blocks:
					continue
				# if it depends on >=7, bad...
				if not a.match(self.x6):
					bad.add(a)
			if bad:
				reporter.add_report(BadRange(pkg, attr, sorted(bad)))
			
			# fun one.
			r = depset.evaluate_depset([], tristate_filter=[])
			bad = []
			for block in r.dnf_solutions():
				block = unstable_unique(block)
				if not any(True for x in block if x.key == "virtual/x11" and not x.blocks):
					continue
				# got ourselves a potential.
				if not any(True for x in block if x.key in self.valid_modx_keys and not x.blocks):
					bad = block
					ported_status = True
					break
					
			if bad:
				for or_block in r.cnf_solutions():
					if not any(True for x in or_block if x.key == "virtual/x11" and not x.blocks):
						continue
					if any(True for x in or_block if x.key in self.valid_modx_keys and not x.blocks):
						break
				else:
					# we've got a standalone virtual/x11
					reporter.add_report(NotPorted(pkg, attr, bad))
					failed.append(attr)
		
		if failed:
			unported.append(pkg)
		
		if len(failed) == 2:
			# no point in trying it out, will fail anyways
			return
		elif not failed and ported_status:
			ported.append(pkg)
				
		skip_depends = "depends" in failed
		skip_rdepends = "rdepends" in failed
		del failed
		
		# ok heres the rules of the road.
		# valid: || ( modx <virtual/x11-7 ), || ( modx virtual/x11 )
		# not valid: >=virtual/x11-7 anywhere, virtual/x11 floating
		# not valid: x11-base/xorg-x11 floating
		
		diuse = pkg.depends.known_conditionals
		riuse = pkg.rdepends.known_conditionals
		deval_cache = {}
		reval_cache = {}

		relevant_profiles = feeder.identify_profiles(pkg)
		if not skip_depends:
			for edepset, profiles in feeder.collapse_evaluate_depset(relevant_profiles, pkg.depends):
				self.process_depset(pkg, "depends", edepset, profiles, query_cache, reporter)
		if not skip_rdepends:
			for edepset, profiles in feeder.collapse_evaluate_depset(relevant_profiles, pkg.rdepends):
				self.process_depset(pkg, "rdepends/pdepends", edepset, profiles, query_cache, reporter)
				
	def process_depset(self, pkg, attr, depset, profiles, query_cache, reporter):
		csolutions = depset.cnf_solutions()
		repo = self.repo
		failed = set()
		for key, profile_name, data in profiles:
			failed.clear()
			virtuals, flags, non_tristate, vfilter, cache, insoluable = data
			for or_block in csolutions:
				if not any(True for x in or_block if x.key == "virtual/x11"):
					continue
			
				# we know a virtual/x11 is in this options.
				# better have a modx node in options, else it's bad.
				modx_candidates = [x for x in or_block if x.key in self.valid_modx_keys]
				for a in modx_candidates:
					if a.blocks:
						# weird.
						continue
					h = hash(a)
					if h in insoluable:
						pass
					elif h in cache:
						break
					elif a not in query_cache:
						query_cache[h] = caching_iter(repo.itermatch(a))
					if any(True for pkg in query_cache[h] if vfilter.match(pkg)):
						# one is visible.
						break
				else:
					failed.update(modx_candidates)
			if failed:
				reporter.add_report(VisibilityCausedNotPorted(pkg, key, profile_name, attr, sorted(failed)))

	def finish(self, *a):
		self.repo = self.profile_filters = self.keywords_filter = None


class SuggestRemoval(base.Result):
	description = "pkg isn't ported, stablize the targets and it can likely go away"
	__slots__ = ("category", "package", "version", "ported")
	def __init__(self, pkg, ported):
		self.category, self.package, self.version = pkg.category, pkg.package, pkg.fullver
		self.ported = tuple(str(x) for x in ported)
	
	def to_str(self):
		return "%s/%s-%s: is unported, potentially remove for [ %s ]" \
			% (self.category, self.package, self.version, ", ".join(self.ported))
	
	def to_xml(self):
		return \
"""<check name="%s">
	<category>%s</category>
	<package>%s</package>
	<version>%s</version>
	<msg>unported, suggest replacing via: %s</msg>
</check>""" % (self.__class__, self.category, self.package, self.version, escape(", ".join(self.ported)))



class BadRange(base.Result):
	description = "check for virtual/x11 atoms that don't match 6.9, implying they require 7.x"
	__slots__ = ("category", "package", "version", "attr", "atom")
	def __init__(self, pkg, attr, atom):
		self.category, self.package, self.version = pkg.category, pkg.package, pkg.fullver
		self.attr = attr
		self.atoms = map(str, atom)
	
	def to_str(self):
		return "%s/%s-%s: attr(%s): atoms don't match 6.9: [ %s ]" % (self.category, self.package, self.version, self.attr, 
			", ".join(self.atoms))
	
	def to_xml(self):
		return \
"""<check name="%s">
	<category>%s</category>
	<package>%s</package>
	<version>%s</version>
	<msg>attr %s has atoms %s, which do not match virtual/x11-6.9</msg>
</check>""" % (self.__class__, self.category, self.package, self.version, self.attr, escape(", ".join(self.atoms)))


class NotPorted(base.Result):
	description = "standalone virtual/x11 atom, not ported."
	__slots__ = ("category", "package", "version", "attr", "or_block")

	def __init__(self, pkg, attr, or_block):
		self.category, self.package, self.version = pkg.category, pkg.package, pkg.fullver
		self.attr = attr
		self.or_block = or_block
	
	def to_str(self):
		return "%s/%s-%s: attr(%s): not ported, standalone virtual/x11 atom detected in an or_block" % (self.category, self.package, self.version, self.attr)
	
	def to_xml(self):
		return \
"""<check name="%s">
	<category>%s</category>
	<package>%s</package>
	<version>%s</version>
	<msg>attr %s, standalone virtual/x11 atom detected in an or_block"</msg>
</check>""" % (self.__class__, self.category, self.package, self.version, self.attr)


class VisibilityCausedNotPorted(base.Result):
	description = "ported, but due to visibility (mask'ing/keywords), knocked back to effectively not ported"
	__slots__ = ("category", "package", "version", "attr", "keyword", "profile", "failed")

	def __init__(self, pkg, keyword, profile, attr, failed):
		self.category, self.package, self.version = pkg.category, pkg.package, pkg.fullver
		self.attr = attr
		self.keyword = keyword
		self.profile = profile
		self.failed = tuple(str(x) for x in failed)
	
	def to_str(self):
		return "%s/%s-%s: %s %s %s: visibility induced unported: fix via making visible [ %s ]" % \
			(self.category, self.package, self.version, self.attr, self.keyword, self.profile,  
			", ".join(self.failed))
	
	def to_xml(self):
		return \
"""<check name="%s">
	<category>%s</category>
	<package>%s</package>
	<version>%s</version>
	<keyword>%s</keyword>
	<profile>%s</profile>
	<msg>attr %s, visibility limiters mean that the following atoms aren't accessible, resulting in non-modular x deps: %s</msg>
</check>""" % (self.category, self.package, self.version, self.keyword, self.profile, self.attr, escape(", ".join(self.failed)))


class NonsolvableDeps(base.Result):
	description = "No potential solution for a depset attribute"
	__slots__ = ("category", "package", "version", "attr", "profile", "keyword", 
		"potentials", "masked")
	
	def __init__(self, pkg, attr, keyword, profile, horked, masked=False):
		self.category = pkg.category
		self.package = pkg.package
		self.version = pkg.fullver
		self.attr = attr
		self.profile = profile
		self.keyword = keyword
		self.potentials = tuple(str(x) for x in stable_unique(horked))
		self.masked = masked
		
	def to_str(self):
		s=' '
		if self.keyword.startswith("~"):
			s=''
		if self.masked:
			s = "masked "+s
		return "%s/%s-%s: %s %s%s: unsolvable %s, solutions: [ %s ]" % \
			(self.category, self.package, self.version, self.attr, s, self.keyword, self.profile,
			", ".join(self.potentials))

	def to_xml(self):
		s = ''
		if self.masked:
			s = "masked, "
		return \
"""<check name="%s">
	<category>%s</category>
	<package>%s</package>
	<version>%s</version>
	<profile>%s</profile>
	<keyword>%s</keyword>
	<msg>%snot solvable for %s- potential solutions, %s</msg>
</check>""" % (self.__class__.__name__, self.category, self.package, self.version,
self.profile, self.keyword, s, self.attr, escape(", ".join(self.potentials)))
