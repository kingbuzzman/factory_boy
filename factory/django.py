# Copyright: See the LICENSE file.


"""factory_boy extensions for use with the Django framework."""


import functools
import io
import logging
import os
import warnings
from collections import defaultdict

from django import __version__ as django_version
from django.contrib.auth.hashers import make_password
from django.core import files as django_files
from django.db import IntegrityError, connections, models
from packaging.version import Version

from . import base, declarations, errors

logger = logging.getLogger('factory.generate')


DEFAULT_DB_ALIAS = 'default'  # Same as django.db.DEFAULT_DB_ALIAS

DJANGO_22 = Version(django_version) < Version('3.0')

_LAZY_LOADS = {}


def get_model(app, model):
    """Wrapper around django's get_model."""
    if 'get_model' not in _LAZY_LOADS:
        _lazy_load_get_model()

    _get_model = _LAZY_LOADS['get_model']
    return _get_model(app, model)


def _lazy_load_get_model():
    """Lazy loading of get_model.

    get_model loads django.conf.settings, which may fail if
    the settings haven't been configured yet.
    """
    from django import apps as django_apps
    _LAZY_LOADS['get_model'] = django_apps.apps.get_model


def connection_supports_bulk_insert(using):
    """
    Does the database support bulk_insert

    There are 2 pieces to this puzzle:
      * The database needs to support `bulk_insert`
      * AND it also needs to be capable of returning all the newly minted objects' id

    If any of these is `False`, the database does NOT support bulk_insert
    """
    connection = connections[using]
    if DJANGO_22:
        can_return_rows_from_bulk_insert = connection.features.can_return_ids_from_bulk_insert
    else:
        can_return_rows_from_bulk_insert = connection.features.can_return_rows_from_bulk_insert
    return (connection.features.has_bulk_insert
            and can_return_rows_from_bulk_insert)


class DjangoOptions(base.FactoryOptions):
    def _build_default_options(self):
        return super()._build_default_options() + [
            base.OptionDefault('django_get_or_create', (), inherit=True),
            base.OptionDefault('database', DEFAULT_DB_ALIAS, inherit=True),
            base.OptionDefault('use_bulk_create', False, inherit=True),
            base.OptionDefault('skip_postgeneration_save', False, inherit=True),
        ]

    def _get_counter_reference(self):
        counter_reference = super()._get_counter_reference()
        if (counter_reference == self.base_factory
                and self.base_factory._meta.model is not None
                and self.base_factory._meta.model._meta.abstract
                and self.model is not None
                and not self.model._meta.abstract):
            # Target factory is for an abstract model, yet we're for another,
            # concrete subclass => don't reuse the counter.
            return self.factory
        return counter_reference

    def get_model_class(self):
        if isinstance(self.model, str) and '.' in self.model:
            app, model_name = self.model.split('.', 1)
            self.model = get_model(app, model_name)

        return self.model


class DjangoModelFactory(base.Factory):
    """Factory for Django models.

    This makes sure that the 'sequence' field of created objects is a new id.

    Possible improvement: define a new 'attribute' type, AutoField, which would
    handle those for non-numerical primary keys.
    """

    _options_class = DjangoOptions

    class Meta:
        abstract = True  # Optional, but explicit.

    @classmethod
    def _load_model_class(cls, definition):

        if isinstance(definition, str) and '.' in definition:
            app, model = definition.split('.', 1)
            return get_model(app, model)

        return definition

    @classmethod
    def _get_manager(cls, model_class):
        if model_class is None:
            raise errors.AssociatedClassError(
                f"No model set on {cls.__module__}.{cls.__name__}.Meta")

        try:
            manager = model_class.objects
        except AttributeError:
            # When inheriting from an abstract model with a custom
            # manager, the class has no 'objects' field.
            manager = model_class._default_manager

        if cls._meta.database != DEFAULT_DB_ALIAS:
            manager = manager.using(cls._meta.database)
        return manager

    @classmethod
    def _generate(cls, strategy, params):
        # Original params are used in _get_or_create if it cannot build an
        # object initially due to an IntegrityError being raised
        cls._original_params = params
        return super()._generate(strategy, params)

    @classmethod
    def _get_or_create(cls, model_class, *args, **kwargs):
        """Create an instance of the model through objects.get_or_create."""
        manager = cls._get_manager(model_class)

        assert 'defaults' not in cls._meta.django_get_or_create, (
            "'defaults' is a reserved keyword for get_or_create "
            "(in %s._meta.django_get_or_create=%r)"
            % (cls, cls._meta.django_get_or_create))

        key_fields = {}
        for field in cls._meta.django_get_or_create:
            if field not in kwargs:
                raise errors.FactoryError(
                    "django_get_or_create - "
                    "Unable to find initialization value for '%s' in factory %s" %
                    (field, cls.__name__))
            key_fields[field] = kwargs.pop(field)
        key_fields['defaults'] = kwargs

        try:
            instance, _created = manager.get_or_create(*args, **key_fields)
        except IntegrityError as e:
            get_or_create_params = {
                lookup: value
                for lookup, value in cls._original_params.items()
                if lookup in cls._meta.django_get_or_create
            }
            if get_or_create_params:
                try:
                    instance = manager.get(**get_or_create_params)
                except manager.model.DoesNotExist:
                    # Original params are not a valid lookup and triggered a create(),
                    # that resulted in an IntegrityError. Follow Django’s behavior.
                    raise e
            else:
                raise e

        return instance

    @classmethod
    def supports_bulk_insert(cls):
        return (cls._meta.use_bulk_create
                and connection_supports_bulk_insert(cls._meta.database))

    @classmethod
    def create(cls, **kwargs):
        """Create an instance of the associated class, with overridden attrs."""
        if not cls.supports_bulk_insert():
            return super().create(**kwargs)

        return cls._bulk_create(1, **kwargs)[0]

    @classmethod
    def create_batch(cls, size, **kwargs):
        if not cls.supports_bulk_insert():
            return super().create_batch(size, **kwargs)

        return cls._bulk_create(size, **kwargs)

    @classmethod
    def _refresh_database_pks(cls, model_cls, objs):
        """
        Before Django 3.0, there is an issue when bulk_insert.

        The issue is that if you create an instance of a model,
        and reference it in another unsaved instance of a model.
        When you create the instance of the first one, the pk/id
        is never updated on the sub model that referenced the first.
        """
        if not DJANGO_22:
            return
        fields = [f for f in model_cls._meta.get_fields()
                  if isinstance(f, models.fields.related.ForeignObject)]
        if not fields:
            return
        for obj in objs:
            for field in fields:
                setattr(obj, field.name, getattr(obj, field.name))

    @classmethod
    def _bulk_create(cls, size, **kwargs):
        models_to_create = cls.build_batch(size, **kwargs)
        collector = DependencyInsertOrderCollector()
        collector.collect(cls, models_to_create)
        collector.sort()
        for model_cls, objs in collector.data.items():
            manager = cls._get_manager(model_cls)
            cls._refresh_database_pks(model_cls, objs)
            manager.bulk_create(objs)
        return models_to_create

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        """Create an instance of the model, and save it to the database."""
        if cls._meta.django_get_or_create:
            return cls._get_or_create(model_class, *args, **kwargs)

        manager = cls._get_manager(model_class)
        return manager.create(*args, **kwargs)

    # DEPRECATED. Remove this override with the next major release.
    @classmethod
    def _after_postgeneration(cls, instance, create, results=None):
        """Save again the instance if creating and at least one hook ran."""
        if create and results and not cls._meta.skip_postgeneration_save:
            warnings.warn(
                f"{cls.__name__}._after_postgeneration will stop saving the instance "
                "after postgeneration hooks in the next major release.\n"
                "If the save call is extraneous, set skip_postgeneration_save=True "
                f"in the {cls.__name__}.Meta.\n"
                "To keep saving the instance, move the save call to your "
                "postgeneration hooks or override _after_postgeneration.",
                DeprecationWarning,
            )
            # Some post-generation hooks ran, and may have modified us.
            instance.save()


class Password(declarations.Transformer):
    def __init__(self, password, *args, **kwargs):
        super().__init__(make_password, password, *args, **kwargs)


class FileField(declarations.BaseDeclaration):
    """Helper to fill in django.db.models.FileField from a Factory."""

    DEFAULT_FILENAME = 'example.dat'

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

    def evaluate(self, instance, step, extra):
        """Fill in the field."""
        filename, content = self._make_content(extra)
        return django_files.File(content.file, filename)


class ImageField(FileField):
    DEFAULT_FILENAME = 'example.jpg'

    def _make_data(self, params):
        # ImageField (both django's and factory_boy's) require PIL.
        # Try to import it along one of its known installation paths.
        from PIL import Image

        width = params.get('width', 100)
        height = params.get('height', width)
        color = params.get('color', 'blue')
        image_format = params.get('format', 'JPEG')
        image_palette = params.get('palette', 'RGB')

        thumb_io = io.BytesIO()
        with Image.new(image_palette, (width, height), color) as thumb:
            thumb.save(thumb_io, format=image_format)
        return thumb_io.getvalue()


class DependencyInsertOrderCollector:
    def __init__(self):
        # Initially, {model: {instances}}, later values become lists.
        self.data = defaultdict(list)
        # Tracks deletion-order dependency for databases without transactions
        # or ability to defer constraint checks. Only concrete model classes
        # should be included, as the dependencies exist only between actual
        # database tables; proxy models are represented here by their concrete
        # parent.
        self.dependencies = defaultdict(set)  # {model: {models}}

    def add(self, objs, source=None, nullable=False):
        """
        Add 'objs' to the collection of objects to be inserted in order.  If the call is
        the result of a cascade, 'source' should be the model that caused it,
        and 'nullable' should be set to True if the relation can be null.
        Return a list of all objects that were not already collected.
        """
        if not objs:
            return []
        new_objs = []
        model = objs[0].__class__
        instances = self.data[model]
        lookup = [id(instance) for instance in instances]
        for obj in objs:
            if not obj._state.adding:
                continue
            if id(obj) not in lookup:
                new_objs.append(obj)
        instances.extend(new_objs)
        # Nullable relationships can be ignored -- they are nulled out before
        # deleting, and therefore do not affect the order in which objects have
        # to be deleted.
        if source is not None and not nullable:
            self.add_dependency(source, model)
        return new_objs

    def add_dependency(self, model, dependency):
        self.dependencies[model._meta.concrete_model].add(
            dependency._meta.concrete_model
        )
        self.data.setdefault(dependency, self.data.default_factory())

    def collect(
        self,
        factory_cls,
        objs,
        source=None,
        nullable=False,
    ):
        """
        Add 'objs' to the collection of objects to be deleted as well as all
        parent instances.  'objs' must be a homogeneous iterable collection of
        model instances (e.g. a QuerySet).  If 'collect_related' is True,
        related objects will be handled by their respective on_delete handler.
        If the call is the result of a cascade, 'source' should be the model
        that caused it and 'nullable' should be set to True, if the relation
        can be null.
        If 'keep_parents' is True, data of parent model's will be not deleted.
        If 'fail_on_restricted' is False, error won't be raised even if it's
        prohibited to delete such objects due to RESTRICT, that defers
        restricted object checking in recursive calls where the top-level call
        may need to collect more objects to determine whether restricted ones
        can be deleted.
        """
        new_objs = self.add(
            objs, source, nullable
        )
        if not new_objs:
            return

        model = new_objs[0].__class__

        # The candidate relations are the ones that come from N-1 and 1-1 relations.
        candidate_relations = (
            f for f in model._meta.get_fields(include_hidden=True)
            if isinstance(f, models.ForeignKey)
        )

        collected_objs = []
        for field in candidate_relations:
            for obj in new_objs:
                val = getattr(obj, field.name)
                if isinstance(val, models.Model):
                    collected_objs.append(val)

        for name, in factory_cls._meta.post_declarations.as_dict().keys():
            for obj in new_objs:
                val = getattr(obj, name, None)
                if isinstance(val, models.Model):
                    collected_objs.append(val)

        if collected_objs:
            new_objs = self.collect(
                factory_cls=factory_cls, objs=collected_objs, source=model
            )

    def sort(self):
        """
        Sort the model instances by the least dependecies to the most dependencies.

        We want to insert the models with no dependencies first, and continue inserting
        using the models that the higher models depend on.
        """
        sorted_models = []
        concrete_models = set()
        models = list(self.data)
        while len(sorted_models) < len(models):
            found = False
            for model in models:
                if model in sorted_models:
                    continue
                dependencies = self.dependencies.get(model._meta.concrete_model)
                if not (dependencies and dependencies.difference(concrete_models)):
                    sorted_models.append(model)
                    concrete_models.add(model._meta.concrete_model)
                    found = True
            if not found:
                logger.debug('dependency order could not be determined')
                return
        self.data = {model: self.data[model] for model in sorted_models}


class mute_signals:
    """Temporarily disables and then restores any django signals.

    Args:
        *signals (django.dispatch.dispatcher.Signal): any django signals

    Examples:
        with mute_signals(pre_init):
            user = UserFactory.build()
            ...

        @mute_signals(pre_save, post_save)
        class UserFactory(factory.Factory):
            ...

        @mute_signals(post_save)
        def generate_users():
            UserFactory.create_batch(10)
    """

    def __init__(self, *signals):
        self.signals = signals
        self.paused = {}

    def __enter__(self):
        for signal in self.signals:
            logger.debug('mute_signals: Disabling signal handlers %r',
                         signal.receivers)

            # Note that we're using implementation details of
            # django.signals, since arguments to signal.connect()
            # are lost in signal.receivers
            self.paused[signal] = signal.receivers
            signal.receivers = []

    def __exit__(self, exc_type, exc_value, traceback):
        for signal, receivers in self.paused.items():
            logger.debug('mute_signals: Restoring signal handlers %r',
                         receivers)

            signal.receivers += receivers
            with signal.lock:
                # Django uses some caching for its signals.
                # Since we're bypassing signal.connect and signal.disconnect,
                # we have to keep messing with django's internals.
                signal.sender_receivers_cache.clear()
        self.paused = {}

    def copy(self):
        return mute_signals(*self.signals)

    def __call__(self, callable_obj):
        if isinstance(callable_obj, base.FactoryMetaClass):
            # Retrieve __func__, the *actual* callable object.
            callable_obj._create = self.wrap_method(callable_obj._create.__func__)
            callable_obj._bulk_create = self.wrap_method(callable_obj._bulk_create.__func__)
            callable_obj._generate = self.wrap_method(callable_obj._generate.__func__)
            return callable_obj

        else:
            @functools.wraps(callable_obj)
            def wrapper(*args, **kwargs):
                # A mute_signals() object is not reentrant; use a copy every time.
                with self.copy():
                    return callable_obj(*args, **kwargs)
            return wrapper

    def wrap_method(self, method):
        @classmethod
        @functools.wraps(method)
        def wrapped_method(*args, **kwargs):
            # A mute_signals() object is not reentrant; use a copy every time.
            with self.copy():
                return method(*args, **kwargs)
        return wrapped_method
