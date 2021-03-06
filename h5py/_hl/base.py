# This file is part of h5py, a Python interface to the HDF5 library.
#
# http://www.h5py.org
#
# Copyright 2008-2013 Andrew Collette and contributors
#
# License:  Standard 3-clause BSD; see "license.txt" for full license terms
#           and contributor agreement.

import posixpath
import warnings
import os
import sys

from h5py import h5d, h5i, h5r, h5p, h5f, h5t

# The high-level interface is serialized; every public API function & method
# is wrapped in a lock.  We re-use the low-level lock because (1) it's fast, 
# and (2) it eliminates the possibility of deadlocks due to out-of-order
# lock acquisition.
from h5py._objects import phil, with_phil

py3 = sys.version_info[0] == 3


def is_hdf5(fname):
    """ Determine if a file is valid HDF5 (False if it doesn't exist). """
    with phil:
        fname = os.path.abspath(fname)

        if os.path.isfile(fname):
            try:
                fname = fname.encode(sys.getfilesystemencoding())
            except (UnicodeError, LookupError):
                pass
            return h5f.is_hdf5(fname)
        return False


def guess_dtype(data):
    """ Attempt to guess an appropriate dtype for the object, returning None
    if nothing is appropriate (or if it should be left up the the array
    constructor to figure out)
    """
    with phil:
        if isinstance(data, h5r.RegionReference):
            return h5t.special_dtype(ref=h5r.RegionReference)
        if isinstance(data, h5r.Reference):
            return h5t.special_dtype(ref=h5r.Reference)
        if type(data) == bytes:
            return h5t.special_dtype(vlen=bytes)
        if type(data) == unicode:
            return h5t.special_dtype(vlen=unicode)

        return None


def default_lapl():
    """ Default link access property list """
    lapl = h5p.create(h5p.LINK_ACCESS)
    fapl = h5p.create(h5p.FILE_ACCESS)
    fapl.set_fclose_degree(h5f.CLOSE_STRONG)
    lapl.set_elink_fapl(fapl)
    return lapl


def default_lcpl():
    """ Default link creation property list """
    lcpl = h5p.create(h5p.LINK_CREATE)
    lcpl.set_create_intermediate_group(True)
    return lcpl

dlapl = default_lapl()
dlcpl = default_lcpl()


class CommonStateObject(object):

    """
        Mixin class that allows sharing information between objects which
        reside in the same HDF5 file.  Requires that the host class have
        a ".id" attribute which returns a low-level ObjectID subclass.

        Also implements Unicode operations.
    """

    @property
    def _lapl(self):
        """ Fetch the link access property list appropriate for this object
        """
        return dlapl

    @property
    def _lcpl(self):
        """ Fetch the link creation property list appropriate for this object
        """
        return dlcpl

    def _e(self, name, lcpl=None):
        """ Encode a name according to the current file settings.

        Returns name, or 2-tuple (name, lcpl) if lcpl is True

        - Binary strings are always passed as-is, h5t.CSET_ASCII
        - Unicode strings are encoded utf8, h5t.CSET_UTF8

        If name is None, returns either None or (None, None) appropriately.
        """
        def get_lcpl(coding):
            lcpl = self._lcpl.copy()
            lcpl.set_char_encoding(coding)
            return lcpl

        if name is None:
            return (None, None) if lcpl else None

        if isinstance(name, bytes):
            coding = h5t.CSET_ASCII
        else:
            try:
                name = name.encode('ascii')
                coding = h5t.CSET_ASCII
            except UnicodeEncodeError:
                name = name.encode('utf8')
                coding = h5t.CSET_UTF8

        if lcpl:
            return name, get_lcpl(coding)
        return name

    def _d(self, name):
        """ Decode a name according to the current file settings.

        - Try to decode utf8
        - Failing that, return the byte string

        If name is None, returns None.
        """
        if name is None:
            return None

        try:
            return name.decode('utf8')
        except UnicodeDecodeError:
            pass
        return name


class _RegionProxy(object):

    """
        Proxy object which handles region references.

        To create a new region reference (datasets only), use slicing syntax:

            >>> newref = obj.regionref[0:10:2]

        To determine the target dataset shape from an existing reference:

            >>> shape = obj.regionref.shape(existingref)

        where <obj> may be any object in the file. To determine the shape of
        the selection in use on the target dataset:

            >>> selection_shape = obj.regionref.selection(existingref)
    """

    def __init__(self, obj):
        self.id = obj.id

    @with_phil
    def __getitem__(self, args):
        if not isinstance(self.id, h5d.DatasetID):
            raise TypeError("Region references can only be made to datasets")
        from . import selections
        selection = selections.select(self.id.shape, args, dsid=self.id)
        return h5r.create(self.id, b'.', h5r.DATASET_REGION, selection._id)

    def shape(self, ref):
        """ Get the shape of the target dataspace referred to by *ref*. """
        with phil:
            sid = h5r.get_region(ref, self.id)
            return sid.shape

    def selection(self, ref):
        """ Get the shape of the target dataspace selection referred to by *ref*
        """
        with phil:
            from . import selections
            sid = h5r.get_region(ref, self.id)
            return selections.guess_shape(sid)


class HLObject(CommonStateObject):

    """
        Base class for high-level interface objects.
    """

    @property
    @with_phil
    def file(self):
        """ Return a File instance associated with this object """
        import files
        return files.File(self.id)

    @property
    @with_phil
    def name(self):
        """ Return the full name of this object.  None if anonymous. """
        return self._d(h5i.get_name(self.id))

    @property
    @with_phil
    def parent(self):
        """Return the parent group of this object.

        This is always equivalent to obj.file[posixpath.dirname(obj.name)].
        ValueError if this object is anonymous.
        """
        if self.name is None:
            raise ValueError("Parent of an anonymous object is undefined")
        return self.file[posixpath.dirname(self.name)]

    @property
    @with_phil
    def id(self):
        """ Low-level identifier appropriate for this object """
        return self._id

    @property
    @with_phil
    def ref(self):
        """ An (opaque) HDF5 reference to this object """
        return h5r.create(self.id, b'.', h5r.OBJECT)

    @property
    @with_phil
    def regionref(self):
        """Create a region reference (Datasets only).

        The syntax is regionref[<slices>]. For example, dset.regionref[...]
        creates a region reference in which the whole dataset is selected.

        Can also be used to determine the shape of the referenced dataset
        (via .shape property), or the shape of the selection (via the
        .selection property).
        """
        return _RegionProxy(self)

    @property
    @with_phil
    def attrs(self):
        """ Attributes attached to this object """
        import attrs
        return attrs.AttributeManager(self)

    @with_phil
    def __init__(self, oid):
        """ Setup this object, given its low-level identifier """
        self._id = oid

    @with_phil
    def __hash__(self):
        return hash(self.id)

    @with_phil
    def __eq__(self, other):
        if hasattr(other, 'id'):
            return self.id == other.id
        return False

    @with_phil
    def __ne__(self, other):
        return not self.__eq__(other)

    def __nonzero__(self):
        with phil:
            return bool(self.id)


class View(object):

    def __init__(self, obj):
        self._obj = obj

    @with_phil
    def __len__(self):
        return len(self._obj)


class KeyView(View):

    @with_phil
    def __contains__(self, what):
        return what in self._obj

    @with_phil
    def __iter__(self):
        for x in self._obj:
            yield x


class ValueView(View):

    def __contains__(self, what):
        raise TypeError("Containership testing doesn't work for values. :(")

    @with_phil
    def __iter__(self):
        for x in self._obj:
            yield self._obj.get(x)


class ItemView(View):

    @with_phil
    def __contains__(self, what):
        if what[0] in self._obj:
            return what[1] == self._obj.get(what[0])
        return False

    @with_phil
    def __iter__(self):
        for x in self._obj:
            yield (x, self._obj.get(x))


class DictCompat(object):

    """
        Contains dictionary-style compatibility methods for groups and
        attributes.
    """

    def get(self, name, default=None):
        """ Retrieve the member, or return default if it doesn't exist """
        with phil:
            try:
                return self[name]
            except KeyError:
                return default

    if py3:
        def keys(self):
            """ Get a view object on member names """
            return KeyView(self)

        def values(self):
            """ Get a view object on member objects """
            return ValueView(self)

        def items(self):
            """ Get a view object on member items """
            return ItemView(self)

    else:
        def keys(self):
            """ Get a list containing member names """
            with phil:
                return list(self)

        def iterkeys(self):
            """ Get an iterator over member names """
            return iter(self)

        def values(self):
            """ Get a list containing member objects """
            with phil:
                return [self.get(x) for x in self]

        def itervalues(self):
            """ Get an iterator over member objects """
            for x in self:
                yield self.get(x)

        def items(self):
            """ Get a list of tuples containing (name, object) pairs """
            with phil:
                return [(x, self.get(x)) for x in self]

        def iteritems(self):
            """ Get an iterator over (name, object) pairs """
            for x in self:
                yield (x, self.get(x))
