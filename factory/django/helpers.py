# -*- coding: utf-8 -*-
# Copyright: See the LICENSE file.


"""factory_boy extensions for use with the Django framework."""

from __future__ import absolute_import, unicode_literals

import datetime
import logging

# from django.contrib.contenttypes.fields import GenericForeignKey
# from django.contrib.postgres.fields import ArrayField, HStoreField
# from django.contrib.postgres.fields.jsonb import JSONField
# from django.contrib.postgres.search import SearchVectorField
try:
    from django.db.models import fields
except ImportError:
    fields = object()

def get_generics():
    from django.contrib.contenttypes.fields import GenericForeignKey
    return GenericForeignKey

import pytz

from .declarations import LazySubFactory, NullableSubFactory
from .base import DjangoModelFactory
from .utils import suppress_autotime

logger = logging.getLogger('factory.generate')


factory_field_by_django_field_type = {
    # Can do little to nothing here, so we skip them completely.
    fields.reverse_related.ManyToManyRel: Skip,
    fields.reverse_related.ManyToOneRel: Skip,
    fields.related.ManyToManyField: Skip,
    fields.reverse_related.OneToOneRel: Skip,
    # GenericForeignKey: Skip,

    # Ensure that this can be nullable
    fields.related.ForeignKey: nullable_factory,
    fields.related.OneToOneField: nullable_factory,

    # Standard Django fields
    fields.DecimalField: lambda field: 0,
    fields.CharField: lambda field: field_value(field, ''),
    fields.EmailField: lambda field: field_value(field, ''),
    fields.TextField: lambda field: field_value(field, ''),
    fields.URLField: lambda field: field_value(field, ''),
    fields.files.FileField: lambda field: field_value(field, ''),
    # https://github.com/django/django/blob/2.0/django/db/models/fields/__init__.py#L136  # noqa
    # If the boolean field's default is NOT_PROVIDED, assign False to it.
    # NOT_PROVIDED is a class that is a placeholder for "no default". We can't
    # use null, because it is a valid default value.
    fields.BooleanField: lambda field: (False if isinstance(field.default, type(fields.NOT_PROVIDED))
        else field_value(field, field.default)),
    fields.NullBooleanField: lambda field: field_value(field, False),
    fields.IntegerField: lambda field: field_value(field, 0),
    fields.BigIntegerField: lambda field: field_value(field, 0),
    fields.SmallIntegerField: lambda field: field_value(field, 0),
    fields.PositiveSmallIntegerField: lambda field: field_value(field, 0),
    fields.PositiveIntegerField: lambda field: field_value(field, 0),
    fields.FloatField: lambda field: field_value(field, 0.0),
    fields.DateTimeField: lambda field: field_value(field, datetime.datetime.now(pytz.utc)),
    fields.DateField: lambda field: field_value(field, datetime.date.today()),
    fields.TimeField: lambda field: field_value(field, datetime.time()),
    fields.files.ImageField: lambda field: None,

    # Postgres specific fields
    # SearchVectorField: lambda field: field_value(field, ''),
    # HStoreField: lambda field: {},
    # JSONField: lambda field: {},
    # ArrayField: lambda field: [],
}


class Skip:
    """
    Placeholder used to indicate that a certain field in a model needs to be
    skipped and not be wired into a Factory.

    Example:
        fields.reverse_related.ManyToManyRel: Skip
    """
    pass


def nullable_factory(field, factory_maker_func=None, unique=None):
    factory_maker_func = factory_maker_func or factory_maker
    sub_factory_klass = NullableSubFactory if field.null else LazySubFactory
    return sub_factory_klass(factory_maker_func, params=(field.related_model,))


def field_value(field, default, unique=False):
    """Get the simplest possible value for this field."""
    # Nullable fields get null.
    if field.null:
        return None
    # Non-nullable character fields that can be blank get an empty string.
    elif isinstance(field, fields.CharField) and field.blank:
        return ''
    # Except for special cases above, use the default value for this field.
    else:
        return default


def factory_maker(model, factory_name=None, options=None, field_lookups=None, **kwargs):
    """
    Dynamically creates a class that inherits from DjangoModelFactory. Defines
    methods and then assigns them to a class created on the fly. The class created
    is a factory for a specific class.

    If we call this function for model Foo, we do: factory_maker(Foo) and get the
    class FooFactory that is a a subclass of DjangoModelFactory.

    If we didn't create it programmatically, we would create it as:
    class FooFactory(DjangoModelFactory)

    :param model :type Model: The django model of the object you're trying to create a factory for
    :param factory_name :type str: The name given to the class (defaults to "{model.__name__}Factory")
    :param options :type dict: Options you want to pass to the Factory._meta
    :param field_lookups :type dict: Field lookup table, maps all the django fields to factory fields
    :return :type [Factory]
    """
    field_lookups = field_lookups or factory_field_by_django_field_type
    factory_name = str(factory_name or model.__name__ + 'Factory')
    options = options or {'model': model}

    logger.debug('Creating factory %s' % (factory_name,))

    # Builds the Meta class of the Factory
    class Meta:
        pass

    # Populates the Meta class object
    for key, value in options.items():
        setattr(Meta, key, value)

    def create(cls, **kwargs):
        with suppress_autotime(cls._meta.model, kwargs.keys()):
            return super(cls, cls).create(**kwargs)

    # Now build the class itself. Specify its fields, Meta, methods, etc.
    # First implement the Meta inner class, the module, and the two methods defined above.
    if '__module__' not in kwargs:
        kwargs['__module__'] = ''
    if 'Meta' not in kwargs:
        kwargs['Meta'] = Meta
    if 'create' not in kwargs:
        kwargs['create'] = classmethod(create)

    # Next, define the fields of the model this factory creates.
    for field in model._meta.get_fields():
        if field.name in ('id'):
            kwargs[field.name] = None
            continue

        FieldType = None
        for klass in field.__class__.__mro__:
            if klass in field_lookups:
                FieldType = field_lookups[klass]
                break

        if FieldType is None:
            raise KeyError('Field %s was not found, please register it' % (field.__class__,))
        elif FieldType == Skip:
            continue

        kwargs[field.name] = FieldType(field)

    # Create a class of name `factory_name` inheriting from `DjangoModelFactory`
    # with the attributes of `attr`, essentially creating something like this:
    #
    # > class `str(factory_name)`(DjangoModelFactory):
    # >     **kwargs
    Factory = type(str(factory_name), (DjangoModelFactory,), kwargs)

    return Factory
