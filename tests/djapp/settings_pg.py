# Copyright: See the LICENSE file.

"""Settings for factory_boy/Django tests."""

from .settings import *

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql_psycopg2',
        'NAME': 'dbtest',
        'USER': 'postgres',
        'HOST': 'localhost',
        'PORT': '5432',
    },
    'replica': {
        'ENGINE': 'django.db.backends.postgresql_psycopg2',
        'NAME': 'dbtest',
        'USER': 'postgres',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}
