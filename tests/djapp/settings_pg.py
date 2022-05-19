# Copyright: See the LICENSE file.

"""Settings for factory_boy/Django tests."""

import os

from .settings import *  # noqa: F401, F403

try:
    # pypy does not support `psycopg2` or `psycopg2-binary`
    # This is a package that only gets installed with pypy, and it needs to be
    # initialized for it to work properly. It mimic `psycopg2` 1-to-1
    from psycopg2cffi import compat
    compat.register()
except ImportError:
    pass

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql_psycopg2',
        'NAME': os.environ.get('POSTGRES_DATABASE', 'factory_boy_test'),
        'USER': os.environ.get('POSTGRES_USER', 'postgres'),
        'PASSWORD': os.environ.get('POSTGRES_PASSWORD', 'password'),
        'HOST': os.environ.get('POSTGRES_HOST', 'localhost'),
        'PORT': os.environ.get('POSTGRES_PORT', '5432'),
    },
    'replica': {
        'ENGINE': 'django.db.backends.postgresql_psycopg2',
        'NAME': os.environ.get('POSTGRES_DATABASE', 'factory_boy_test') + '_rp',
        'USER': os.environ.get('POSTGRES_USER', 'postgres'),
        'PASSWORD': os.environ.get('POSTGRES_PASSWORD', 'password'),
        'HOST': os.environ.get('POSTGRES_HOST', 'localhost'),
        'PORT': os.environ.get('POSTGRES_PORT', '5432'),
    }
}
