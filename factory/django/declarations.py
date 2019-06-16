# -*- coding: utf-8 -*-
# Copyright: See the LICENSE file.


"""factory_boy extensions for use with the Django framework."""

from __future__ import absolute_import, unicode_literals

import io
import logging
import os

from .. import base, declarations

try:
    from django.core import files as django_files
except ImportError as e:  # pragma: no cover
    django_files = None
    import_failure = e


logger = logging.getLogger('factory.generate')


def require_django():
    """Simple helper to ensure Django is available."""
    if django_files is None:  # pragma: no cover
        raise import_failure


class FileField(declarations.ParameteredAttribute):
    """Helper to fill in django.db.models.FileField from a Factory."""

    DEFAULT_FILENAME = 'example.dat'

    def __init__(self, **defaults):
        require_django()
        super(FileField, self).__init__(**defaults)

    def _make_data(self, params):
        """Create data for the field."""
        return params.get('data', b'')

    def _make_content(self, params):
        path = ''

        _content_params = [params.get('from_path'), params.get('from_file'), params.get('from_func')]
        if len([p for p in _content_params if p]) > 1:
            raise ValueError(
                "At most one argument from 'from_file', 'from_path', and 'from_func' should "
                "be non-empty when calling factory.django.FileField."
            )

        if params.get('from_path'):
            path = params['from_path']
            with open(path, 'rb') as f:
                content = django_files.base.ContentFile(f.read())

        elif params.get('from_file'):
            f = params['from_file']
            content = django_files.File(f)
            path = content.name

        elif params.get('from_func'):
            func = params['from_func']
            content = django_files.File(func())
            path = content.name

        else:
            data = self._make_data(params)
            content = django_files.base.ContentFile(data)

        if path:
            default_filename = os.path.basename(path)
        else:
            default_filename = self.DEFAULT_FILENAME

        filename = params.get('filename', default_filename)
        return filename, content

    def generate(self, step, params):
        """Fill in the field."""
        # Recurse into a DictFactory: allows users to have some params depending
        # on others.
        params = step.recurse(base.DictFactory, params, force_sequence=step.sequence)
        filename, content = self._make_content(params)
        return django_files.File(content.file, filename)


class ImageField(FileField):
    DEFAULT_FILENAME = 'example.jpg'

    def _make_data(self, params):
        # ImageField (both django's and factory_boy's) require PIL.
        # Try to import it along one of its known installation paths.
        try:
            from PIL import Image
        except ImportError:
            import Image

        width = params.get('width', 100)
        height = params.get('height', width)
        color = params.get('color', 'blue')
        image_format = params.get('format', 'JPEG')

        thumb_io = io.BytesIO()
        with Image.new('RGB', (width, height), color) as thumb:
            thumb.save(thumb_io, format=image_format)
        return thumb_io.getvalue()
