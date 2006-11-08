# Copyright: 2006 Brian Harring <ferringb@gmail.com>
# License: GPL2


"""Core classes and interfaces.

This defines a couple of standard feed types and scopes. Currently
feed types are strings and scopes are integers, but you should use the
symbolic names wherever possible (everywhere except for adding a new
feed type) since this might change in the future. Scopes are integers,
but do not rely on that either.

Feed types have to match exactly. Scopes are ordered: they define a
minimally accepted scope, and for transforms the output scope is
identical to the input scope.
"""


import sys

from pkgcore.config import configurable
from pkgcore.util import formatters
from pkgcore.util.demandload import demandload
demandload(globals(), "logging time ")


repository_feed = "repo"
category_feed = "cat"
package_feed = "cat/pkg"
versioned_feed = "cat/pkg-ver"

# Currently "plug" probably relies on these being valid list indices
# (and on there not being a lot of them).
version_scope, package_scope, category_scope, repository_scope = range(4)


class Addon(object):

    """Base class for extra functionality for pcheck other than a check.

    The checkers can depend on one or more of these. They will get
    called at various points where they can extend pcheck (if any
    active checks depend on the addon).

    These methods are not part of the checker interface because that
    would mean addon functionality shared by checkers would run twice.
    They are not plugins because they do not do anything useful if no
    checker depending on them is active.

    This interface is not finished. Expect it to grow more methods
    (but if not overridden they will be no-ops).

    @cvar required_addons: sequence of addons this one depends on.
    """

    required_addons = ()

    def __init__(self, options, *args):
        """Initialize.

        An instance of every addon in required_addons is passed as extra arg.

        @param options: the optparse values.
        """
        self.options = options

    @staticmethod
    def mangle_option_parser(parser):
        """Add extra options and/or groups to the option parser.

        This hook is always triggered, even if the checker is not
        activated (because it runs before the commandline is parsed).

        @param parser: an C{OptionParser} instance.
        """

    @staticmethod
    def check_values(values):
        """Postprocess the optparse values.

        Should raise C{optparse.OptionValueError} on failure.

        This is only called for addons that are enabled, but before
        they are instantiated.
        """


class Template(Addon):

    """Base template for a check."""

    scope = 0

    def feed(self, chunk, reporter):
        raise NotImplementedError


class Result(object):

    def __str__(self):
        try:
            return self.to_str()
        except NotImplementedError:
            return "result from %s" % self.__class__.__name__
    
    def to_str(self):
        raise NotImplementedError
    
    def to_xml(self):
        raise NotImplementedError

    def _store_cp(self, pkg):
        self.category = pkg.category
        self.package = pkg.package
    
    def _store_cpv(self, pkg):
        self._store_cp(pkg)
        self.version = pkg.fullver


class ReporterInitError(Exception):
    """Raise this if a reporter factory fails."""


class Reporter(object):

    def add_report(self, result):
        raise NotImplementedError(self.add_report)

    def start(self):
        pass

    def finish(self):
        pass


class StrReporter(Reporter):

    def __init__(self, out):
        """Initialize.

        @type out: L{pkgcore.util.formatters.Formatter}.
        """
        Reporter.__init__(self)
        self.out = out
        self.first_report = True

    def add_report(self, result):
        if self.first_report:
            self.out.write()
            self.first_report = False
        self.out.write(result.to_str())

    def finish(self):
        if not self.first_report:
            self.out.write()


class FancyReporter(Reporter):

    def __init__(self, out):
        """Initialize.

        @type out: L{pkgcore.util.formatters.Formatter}.
        """
        Reporter.__init__(self)
        self.out = out
        self.key = None

    def add_report(self, result):
        cat = getattr(result, 'category', None)
        pkg = getattr(result, 'package', None)
        if cat is None or pkg is None:
            key = 'unknown'
        else:
            key = '%s/%s' % (cat, pkg)
        if key != self.key:
            self.out.write()
            self.out.write(self.out.bold, key)
            self.key = key
        self.out.first_prefix.append('  ')
        self.out.later_prefix.append('    ')
        self.out.write(
            self.out.fg('yellow'), result.__class__.__name__, self.out.reset,
            ': ', result.to_str())
        self.out.first_prefix.pop()
        self.out.later_prefix.pop()


class XmlReporter(Reporter):

    def __init__(self, out):
        """Initialize.

        @type out: L{pkgcore.util.formatters.Formatter}.
        """
        Reporter.__init__(self)
        self.out = out

    def start(self):
        self.out.write('<checks>')

    def add_report(self, result):
        self.out.write(result.to_xml())

    def finish(self):
        self.out.write('</checks>')


class MultiplexReporter(Reporter):

    def __init__(self, *reporters):
        if len(reporters) < 2:
            raise ValueError("need at least two reporters")
        Reporter.__init__(self)
        self.reporters = tuple(reporters)

    def start(self):
        for x in self.reporters:
            x.start()

    def add_report(self, result):
        for x in self.reporters:
            x.add_report(result)

    def finish(self):
        for x in self.reporters:
            x.finish()


def make_configurable_reporter_factory(klass):
    @configurable({'dest': 'str'}, typename='pcheck_reporter_factory')
    def configurable_reporter_factory(dest=None):
        if dest is None:
            return klass
        def reporter_factory(out):
            try:
                f = open(dest, 'w')
            except (IOError, OSError), e:
                raise ReporterInitError('Cannot write to %r (%s)' % (dest, e))
            return klass(formatters.PlainTextFormatter(f))
        return reporter_factory
    return configurable_reporter_factory

xml_reporter = make_configurable_reporter_factory(XmlReporter)
plain_reporter = make_configurable_reporter_factory(StrReporter)
fancy_reporter = make_configurable_reporter_factory(FancyReporter)

@configurable({'reporters': 'refs:pcheck_reporter_factory'},
              typename='pcheck_reporter_factory')
def multiplex_reporter(reporters):
    def make_multiplex_reporter(out):
        return MultiplexReporter(*list(factory(out) for factory in reporters))
    return make_multiplex_reporter


def plug(sinks, transforms, sources, reporter, debug=None):
    """Plug together a pipeline.

    sinks are check instances, transforms are transform instances,
    sources are source instances. For now at least.

    @param sinks: Sequence of check instances.
    @param transforms: Sequence of transform instances.
    @param sources: Sequence of source instances.
    @param reporter: reporter instance.
    @param debug: A logging function or C{None}.
    @returns: a sequence of sinks that are out of scope, a sequence of sinks
        that cannot be reached through transforms, a sequence of running sinks,
        a sequence of pipes.
    """
    # The general idea is we will usually not have a large number of
    # types, so we can use a reasonably straightforward bruteforce
    # approach. We may have quite a bunch of sources, transforms or
    # sinks, but we only care about the cheapest of those for a type.

    # The plan:
    # - Build a matrix with the cheapest sequence of
    #   transforms for every source and target type.
    # - Use this matrix to build all runnable pipes: pipes that start at one
    #   of our sources and include at least one of our sinks.
    # - Report any sinks we cannot drive and throw those away.
    # - If we have one, return the cheapest single pipe driving all
    #   (remaining) sinks.
    # - Find the cheapest combination of pipes that drives all sinks
    #   and return that.

    # We prefer a single pipeline because this increases the
    # readability of our output.

    # TODO maybe optimize scope handling.
    # This was bolted on as an afterthought, and it shows.

    if debug is not None:
        starttime = time.time()

    # Figure out the best available scope.
    best_source_scope = max(source.scope for source in sources)
    # Throw away any checks that we definitely cannot drive.
    lowest_sink_scope = sinks[0].scope
    good_sinks = []
    out_of_scope_sinks = []
    for sink in sinks:
        if sink.scope > best_source_scope:
            out_of_scope_sinks.append(sink)
        else:
            good_sinks.append(sink)
            lowest_sink_scope = min(lowest_sink_scope, sink.scope)
    if not good_sinks:
        # No point in continuing.
        return out_of_scope_sinks, (), (), ()
    sinks = good_sinks
    # We cannot do the same for sources lower than the *highest* sink,
    # since we may end up driving the sinks using multiple sources.
    # But we can throw away the sinks lower than all sources.
    sources = list(s for s in sources if s.scope >= lowest_sink_scope)
    if not sources:
        # No usable sources, abort.
        return out_of_scope_sinks, sinks, (), ()

    # Map from (scope, sink typename) to sequence of sinks.
    sink_map = {}
    for sink in sinks:
        sink_map.setdefault((sink.scope, sink.feed_type), []).append(sink)

    # Map from scope, source typename to cheapest source.
    source_map = {}
    for new_source in sources:
        current_source = source_map.get((new_source.scope,
                                         new_source.feed_type))
        if current_source is None or current_source.cost > new_source.cost:
            source_map[new_source.scope, new_source.feed_type] = new_source

    # Set of all types.
    types = set(typename for scope, typename in source_map).union(
        typename for scope, typename in sink_map)
    for transform in transforms:
        for source, dest, min_scope, cost in transform.transforms:
            types.add(source)
            types.add(dest)

    # type_matrix[scope, source, dest] -> (cost, transforms)
    type_matrix = {}

    # Initialize with basic transforms.
    for transform in transforms:
        for source, dest, scope, cost in transform.transforms:
            # Skip if out of scope, bump to lowest sink scope.
            if scope > best_source_scope:
                continue
            if scope < lowest_sink_scope:
                scope = lowest_sink_scope
            # Pick the cheapest option if more than one basic
            # transform handles this.
            current_pipe = type_matrix.get((scope, source, dest))
            if current_pipe is None or current_pipe[0] > cost:
                type_matrix[scope, source, dest] = (cost, (transform,))
                # Throw away anything with stricter scope requirements
                # that we can stand in for.
                for compat_scope in xrange(scope + 1, best_source_scope + 1):
                    current_pipe = type_matrix.get((scope, source, dest))
                    if current_pipe is not None and current_pipe[0] < cost:
                        # The existing entry is better.
                        break
                    # We are at least as good as the existing entry.
                    type_matrix[scope, source, dest] = (cost, (transform,))

    if debug is not None:
        debug('base type matrix:')
        for (scope, source, dest), (cost, pipe) in type_matrix.iteritems():
            debug('%s: %s -> %s : %s (%s)', scope, source, dest, pipe, cost)
    # Keep trying to build cheaper transforms.
    while True:
        progress = False
        for source in types:
            for dest in types:
                if source == dest:
                    continue
                current_pipe = None
                for scope in xrange(lowest_sink_scope, best_source_scope + 1):
                    new_current = type_matrix.get((scope, source, dest))
                    if new_current is not None:
                        if (current_pipe is None or
                            current_pipe[0] >= new_current[0]):
                            # The new pipe is better, use it as "current".
                            current_pipe = new_current
                        else:
                            # A pipe we hit earlier with lower
                            # requirements is better than this one.
                            progress = True
                            type_matrix[scope, source, dest] = current_pipe
                    for halfway in types:
                        first_half_pipe = type_matrix.get(
                            (scope, source, halfway))
                        if first_half_pipe is None:
                            continue
                        second_half_pipe = type_matrix.get(
                            (scope, halfway, dest))
                        if second_half_pipe is None:
                            continue
                        new_cost = first_half_pipe[0] + second_half_pipe[0]
                        if current_pipe is None or new_cost < current_pipe[0]:
                            progress = True
                            type_matrix[scope, source, dest] = current_pipe = (
                                new_cost,
                                first_half_pipe[1] + second_half_pipe[1])
                            # Do not break out of the loop: we may hit a
                            # combination that is even cheaper.
        if not progress:
            break

    if debug is not None:
        debug('full type matrix:')
        for (scope, source, dest), (cost, pipe) in type_matrix.iteritems():
            debug('%s: %s -> %s : %s (%s)', scope, source, dest, pipe, cost)

    # Tuples of (price, scope, (visited, types))
    # Includes sources with no "direct" sinks for simplicity.
    pipes = []
    unprocessed = list((source.cost, source.scope, (source.feed_type,))
                       for source in source_map.itervalues())
    if debug is not None:
        for pipe in unprocessed:
            debug('initial: %r', pipe)
    # Try to grow longer pipes.
    while unprocessed:
        cost, scope, pipe = unprocessed.pop(0)
        pipes.append((cost, scope, pipe))
        for sink_scope, sink_type in sink_map:
            if sink_type in pipe or sink_scope > scope:
                continue
            halfpipe = type_matrix.get((scope, pipe[-1], sink_type))
            if halfpipe is not None:
                unprocessed.append((
                        cost + halfpipe[0], scope, pipe + (sink_type,)))
                if debug is not None:
                    debug('growing %r with %r', pipe, sink_type)

    # Check if we have unreachable types:
    reachables = {}
    unreachables = []
    for (sink_scope, sink_type), sinks in sink_map.iteritems():
        for cost, pipe_scope, pipe in pipes:
            if pipe_scope >= sink_scope and sink_type in pipe:
                reachables[sink_scope, sink_type] = sinks
                break
        else:
            unreachables.extend(sinks)
    if not reachables:
        # No reachable sinks, abort.
        return out_of_scope_sinks, unreachables, (), ()
    sink_map = reachables

    # Try to find a single pipeline that drives everything we can drive.
    best_pipe = None
    best_cost = 0
    best_scope = 0
    # And build up the map from (scope, sink_type) to (cost, pipe) tuples
    sink_pipes = {}
    for cost, scope, pipe in pipes:
        for sink_scope, sink_type in sink_map:
            if sink_scope > scope or sink_type not in pipe:
                # Found sinks that don't run with this pipe.
                break
        else:
            if best_pipe is None or best_cost > cost:
                best_pipe = pipe
                best_cost = cost
                best_scope = scope
        for passed_type in pipe:
            for sink_scope in xrange(lowest_sink_scope, scope + 1):
                sink_pipes.setdefault((sink_scope, passed_type), []).append(
                    (cost, scope, pipe))

    if best_pipe is not None:
        to_run = [(best_scope, best_pipe)]
    else:
        def generate_pipes(pipes, todo):
            if not todo:
                yield pipes
                return
            for scope, sink_type in todo:
                for cost, pipe_scope, pipe in sink_pipes[scope, sink_type]:
                    new_todo = set(todo)
                    new_todo.difference_update(
                        (sink_scope, sink_type)
                        for sink_scope in xrange(
                            lowest_sink_scope, pipe_scope + 1)
                        for sink_type in pipe)
                    for res in generate_pipes(pipes + [(cost, scope, pipe)],
                                              new_todo):
                        yield res

        best_pipes = None
        best_cost = 0
        best_scope = None
        for pipelist in generate_pipes([], set(sink_map)):
            cost = sum(cost for cost, scope, pipe in pipelist)
            if best_pipes is None or best_cost > cost:
                best_pipes = pipelist
                best_cost = cost
                best_scope = scope

        # Just an assert, since all types in all_sink_types are reachable.
        assert best_pipes is not None
        to_run = list((scope, pipe) for cost, scope, pipe in best_pipes)

    sinks = []
    for sinks_chunk in sink_map.itervalues():
        sinks.extend(sinks_chunk)
    good_sinks = sinks[:]
    actual_pipes = []
    for scope, pipe in to_run:
        if debug is not None:
            debug('running %r (%s)', pipe, scope)
        current_type = pipe[0]
        source = source_map[scope, current_type]
        tail = source.feed()
        # Everything except for the source, reversed.
        types_left = list(pipe[-1:0:-1])
        while True:
            todo = []
            if debug is not None:
                debug('current sinks: %r', sinks)
            for sink in sinks:
                if sink.feed_type != current_type or sink.scope > scope:
                    todo.append(sink)
                else:
                    if debug is not None:
                        debug('plugging %r', sink)
                    tail = sink.feed(tail, reporter)
                    assert tail is not None, '%r is not generating' % (sink,)
            sinks = todo
            if not types_left:
                break
            new_type = types_left.pop()
            for transform in type_matrix[scope, current_type, new_type][1]:
                for (source_type, target_type, trans_scope,
                     cost) in transform.transforms:
                    if source_type == current_type:
                        if debug is not None:
                            debug('going from %s to %s',
                                  current_type, target_type)
                        current_type = target_type
                        break
                else:
                    assert False, 'unreachable'
                assert scope >= trans_scope
                tail = transform.transform(tail)
        actual_pipes.append(tail)
    assert not sinks, '%r left' % (sinks,)
    if debug is not None:
        debug('Plugged in %s seconds ', time.time() - starttime)
    return out_of_scope_sinks, unreachables, good_sinks, actual_pipes
