"""
Data models
"""
import os
import re

import fnmatch
import semver
import six

from click import ParamType
from click.types import StringParamType

_version_search_pattern = r"(^[=><]{1,2})(.*)"
_content_guid_pattern = r"([^,]*),?(.*)"


class BuildStatus(object):
    NEEDS_BUILD = "NEEDS_BUILD" # marked for build
    RUNNING = "RUNNING" # running now
    ABORTED = "ABORTED" # cancelled while running
    COMPLETE = "COMPLETE" # completed successfully
    ERROR = "ERROR" # completed with an error

    _all = [
        NEEDS_BUILD,
        RUNNING,
        ABORTED,
        COMPLETE,
        ERROR
    ]


class AppMode(object):
    """
    Data class defining an "app mode" as understood by RStudio
    Connect
    """

    def __init__(self, ordinal, name, text, ext=None):
        self._ordinal = ordinal
        self._name = name
        self._text = text
        self._ext = ext

    def ordinal(self):
        return self._ordinal

    def name(self):
        return self._name

    def desc(self):
        return self._text

    def extension(self):
        return self._ext

    def __str__(self):
        return self.name()

    def __repr__(self):
        return self.desc()


class AppModes(object):
    """
    Enumeration-like collection of known `AppMode`s with lookup
    functions
    """

    UNKNOWN = AppMode(0, "unknown", "<unknown>")
    SHINY = AppMode(1, "shiny", "Shiny App", ".R")
    RMD = AppMode(3, "rmd-static", "R Markdown", ".Rmd")
    SHINY_RMD = AppMode(2, "rmd-shiny", "Shiny App (Rmd)")
    STATIC = AppMode(4, "static", "Static HTML", ".html")
    PLUMBER = AppMode(5, "api", "API")
    TENSORFLOW = AppMode(6, "tensorflow-saved-model", "TensorFlow Model")
    JUPYTER_NOTEBOOK = AppMode(7, "jupyter-static", "Jupyter Notebook", ".ipynb")
    PYTHON_API = AppMode(8, "python-api", "Python API")
    DASH_APP = AppMode(9, "python-dash", "Dash Application")
    STREAMLIT_APP = AppMode(10, "python-streamlit", "Streamlit Application")
    BOKEH_APP = AppMode(11, "python-bokeh", "Bokeh Application")
    PYTHON_FASTAPI = AppMode(12, "python-fastapi", "Python FastAPI")

    _modes = [
        UNKNOWN,
        SHINY,
        RMD,
        SHINY_RMD,
        STATIC,
        PLUMBER,
        TENSORFLOW,
        JUPYTER_NOTEBOOK,
        PYTHON_API,
        DASH_APP,
        STREAMLIT_APP,
        BOKEH_APP,
        PYTHON_FASTAPI,
    ]

    @classmethod
    def get_by_ordinal(cls, ordinal, return_unknown=False):
        """Get an AppMode by its associated ordinal (integer)"""
        return cls._find_by(
            lambda mode: mode.ordinal() == ordinal,
            "with ordinal %s" % ordinal,
            return_unknown,
        )

    @classmethod
    def get_by_name(cls, name, return_unknown=False):
        """Get an AppMode by name"""
        return cls._find_by(lambda mode: mode.name() == name, "named %s" % name, return_unknown)

    @classmethod
    def get_by_extension(cls, extension, return_unknown=False):
        """Get an app mode by its associated extension"""
        # We can't allow a lookup by None since some modes have that for an extension.
        if extension is None:
            if return_unknown:
                return cls.UNKNOWN
            raise ValueError("No app mode with extension %s" % extension)

        return cls._find_by(
            lambda mode: mode.extension() == extension,
            "with extension: %s" % extension,
            return_unknown,
        )

    @classmethod
    def _find_by(cls, predicate, message, return_unknown):
        for mode in cls._modes:
            if predicate(mode):
                return mode
        if return_unknown:
            return cls.UNKNOWN
        raise ValueError("No app mode %s" % message)


class GlobMatcher(object):
    """
    A simplified means of matching a path against a glob pattern.  The key
    limitation is that we support at most one occurrence of the `**` pattern.
    """

    def __init__(self, pattern):
        if pattern.endswith("/**/*"):
            # Note: the index used here makes sure the pattern has a trailing
            # slash.  We want that.
            self._pattern = pattern[:-4]
            self.matches = self._match_with_starts_with
        else:
            self._pattern_parts, self._wildcard_index = self._to_parts_list(pattern)
            self.matches = self._match_with_list_parts

    @staticmethod
    def _to_parts_list(pattern):
        """
        Converts a glob expression into a list, with an entry for each directory
        level.  Each entry will be either a string, in which case an equality
        check for that directory entry, or a regular expression, in which case
        matching will be used.  The string, '**', is special but we don't alter
        it here.  We do return its index.

        :param pattern: the glob pattern to pull apart.
        :return: a list of pattern pieces and the index of the special '**' pattern.
        The index will be None if `**` is never found.
        """
        parts = pattern.split(os.path.sep)
        depth_wildcard_index = None
        for index, name in enumerate(parts):
            if name == "**":
                if depth_wildcard_index is not None:
                    raise ValueError('Only one occurrence of the "**" pattern is allowed.')
                depth_wildcard_index = index
            elif any(ch in name for ch in "*?["):
                parts[index] = re.compile(r"\A" + fnmatch.translate(name))
        return parts, depth_wildcard_index

    def _match_with_starts_with(self, path):
        return path.startswith(self._pattern)

    def _match_with_list_parts(self, path):
        parts = path.split(os.path.sep)

        def items_match(i1, i2):
            if i2 >= len(parts):
                return False
            if isinstance(self._pattern_parts[i1], six.string_types):
                return self._pattern_parts[i1] == parts[i2]
            return self._pattern_parts[i1].match(parts[i2]) is not None

        wildcard_index = len(self._pattern_parts) if self._wildcard_index is None else self._wildcard_index

        # Top-down...
        for index in range(wildcard_index):
            if not items_match(index, index):
                return False

        if self._wildcard_index is None:
            return len(self._pattern_parts) == len(parts)

        # Now, bottom-up...
        pattern_index = len(self._pattern_parts) - 1
        part_index = len(parts) - 1

        while pattern_index > wildcard_index and part_index >= 0:
            if not items_match(pattern_index, part_index):
                return False
            pattern_index = pattern_index - 1
            part_index = part_index - 1

        return pattern_index == wildcard_index


class GlobSet(object):
    """
    Matches against a set of `GlobMatcher` patterns
    """

    def __init__(self, patterns):
        self._matchers = [GlobMatcher(pattern) for pattern in patterns]

    def matches(self, path):
        """
        Determines whether the given path is matched by any of our glob
        expressions.

        :param path: the path to test.
        :return: True, if the given path matches any of our glob patterns.
        """
        return any(matcher.matches(path) for matcher in self._matchers)


# Strip quotes from string arguments that might be passed in by jq
#  without the -r flag
class StrippedString(StringParamType):
    name = "StrippedString"

    def convert(self, value, param, ctx):
        value = super(StrippedString, self).convert(value, param, ctx)
        return value.strip("\"\'")


class ContentGuidWithBundle(StrippedString):
    name = "ContentGuidWithBundle"

    def __init__(self, guid:str=None, bundle_id:str=None):
        self.guid = guid
        self.bundle_id = bundle_id

    def __repr__(self):
        if self.bundle_id:
            return "%s,%s" % (self.guid, self.bundle_id)
        return self.guid

    def convert(self, value, param, ctx):
        value = super(ContentGuidWithBundle, self).convert(value, param, ctx)
        if isinstance(value, ContentGuidWithBundle):
            return value
        if isinstance(value, str):
            m = re.match(_content_guid_pattern, value)
            if m is not None:
                self.guid = m.group(1)
                if len(m.groups()) == 2:
                    try:
                        int(m.group(2))
                    except ValueError:
                        self.fail("Failed to parse bundle_id. Expected Int, but found: %s" % m.group(2))
                    self.bundle_id = m.group(2)
        return self


class VersionSearchFilter(ParamType):
    name = "VersionSearchFilter"

    def __init__(self, name:str=None, comp:str=None, vers:str=None):
        self.name = name
        self.comp = comp
        self.vers = vers

    # https://click.palletsprojects.com/en/8.0.x/api/#click.ParamType
    def convert(self, value, param, ctx):
        if isinstance(value, VersionSearchFilter):
            return value

        if isinstance(value, str):
            m = re.match(_version_search_pattern, value)
            if m is not None:
                self.comp = m.group(1)
                self.vers = m.group(2)

                if self.comp in ["<<", "<>", "><", ">>", "=<", "=>", "="]:
                    self.fail("Failed to parse verison filter: %s is not a valid comparitor" % self.comp)

                try:
                    semver.parse(self.vers)
                except ValueError:
                    self.fail("Failed to parse version info: %s" % self._vers)
                return self

        self.fail("Failed to parse version filter %s" % value)
