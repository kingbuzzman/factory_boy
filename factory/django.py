# Copyright: See the LICENSE file.


"""factory_boy extensions for use with the Django framework."""


from collections import defaultdict
import functools
import io
from itertools import chain
import logging
import os
import warnings

from django.contrib.auth.hashers import make_password
from django.core import files as django_files
from django.db import IntegrityError, models, connections

from . import base, declarations, errors

logger = logging.getLogger('factory.generate')


DEFAULT_DB_ALIAS = 'default'  # Same as django.db.DEFAULT_DB_ALIAS


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


class DjangoOptions(base.FactoryOptions):
    def _build_default_options(self):
        return super()._build_default_options() + [
            base.OptionDefault('django_get_or_create', (), inherit=True),
            base.OptionDefault('database', DEFAULT_DB_ALIAS, inherit=True),
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
        connection = connections[cls._meta.database]
        return (connection.features.has_bulk_insert
                and connection.features.can_return_rows_from_bulk_insert)

    @classmethod
    def create(cls, **kwargs):
        """Create an instance of the associated class, with overridden attrs."""
        if not cls.supports_bulk_insert():
            return super().create(**kwargs)

        return cls.create_batch(1, **kwargs)[0]

    @classmethod
    def create_batch(cls, size, **kwargs):
        if not cls.supports_bulk_insert():
            return super().create_batch(size, **kwargs)

        models_to_create = cls.build_batch(size, **kwargs)
        collector = Collector('default')
        collector.collect(models_to_create)
        collector.sort()
        for model_cls, objs in collector.data.items():
            manager = cls._get_manager(model_cls)
            for instance in objs:
                models.signals.pre_save.send(model_cls, instance=instance, created=True)
            manager.bulk_create(objs)
            for instance in objs:
                models.signals.post_save.send(model_cls, instance=instance, created=True)
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


def get_candidate_relations_to_delete(opts):
    # The candidate relations are the ones that come from N-1 and 1-1 relations.
    # N-N  (i.e., many-to-many) relations aren't candidates for deletion.
    return (
        f
        for f in opts.get_fields(include_hidden=True)
        if isinstance(f, models.ForeignKey)
    )


class Collector:
    def __init__(self, using):
        self.using = using
        # Initially, {model: {instances}}, later values become lists.
        self.data = defaultdict(list)
        # {model: {(field, value): {instances}}}
        self.field_updates = defaultdict(functools.partial(defaultdict, set))
        # {model: {field: {instances}}}
        self.restricted_objects = defaultdict(functools.partial(defaultdict, set))
        # fast_deletes is a list of queryset-likes that can be deleted without
        # fetching the objects into memory.
        self.fast_deletes = []

        # Tracks deletion-order dependency for databases without transactions
        # or ability to defer constraint checks. Only concrete model classes
        # should be included, as the dependencies exist only between actual
        # database tables; proxy models are represented here by their concrete
        # parent.
        self.dependencies = defaultdict(set)  # {model: {models}}

    def add(self, objs, source=None, nullable=False, reverse_dependency=False):
        """
        Add 'objs' to the collection of objects to be deleted.  If the call is
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
            if obj.pk:
                continue
            if id(obj) not in lookup:
                new_objs.append(obj)
        # import ipdb; ipdb.sset_trace()
        instances.extend(new_objs)
        # Nullable relationships can be ignored -- they are nulled out before
        # deleting, and therefore do not affect the order in which objects have
        # to be deleted.
        if source is not None and not nullable:
            self.add_dependency(source, model, reverse_dependency=reverse_dependency)
        # if not nullable:
        #     import ipdb; ipdb.sset_trace()
        #     self.add_dependency(source, model, reverse_dependency=reverse_dependency)
        return new_objs

    def add_dependency(self, model, dependency, reverse_dependency=False):
        if reverse_dependency:
            model, dependency = dependency, model
        self.dependencies[model._meta.concrete_model].add(
            dependency._meta.concrete_model
        )
        self.data.setdefault(dependency, self.data.default_factory())

    def add_field_update(self, field, value, objs):
        """
        Schedule a field update. 'objs' must be a homogeneous iterable
        collection of model instances (e.g. a QuerySet).
        """
        if not objs:
            return
        model = objs[0].__class__
        self.field_updates[model][field, value].update(objs)

    def add_restricted_objects(self, field, objs):
        if objs:
            model = objs[0].__class__
            self.restricted_objects[model][field].update(objs)

    def clear_restricted_objects_from_set(self, model, objs):
        if model in self.restricted_objects:
            self.restricted_objects[model] = {
                field: items - objs
                for field, items in self.restricted_objects[model].items()
            }

    def clear_restricted_objects_from_queryset(self, model, qs):
        if model in self.restricted_objects:
            objs = set(
                qs.filter(
                    pk__in=[
                        obj.pk
                        for objs in self.restricted_objects[model].values()
                        for obj in objs
                    ]
                )
            )
            self.clear_restricted_objects_from_set(model, objs)

    def _has_signal_listeners(self, model):
        return signals.pre_delete.has_listeners(
            model
        ) or signals.post_delete.has_listeners(model)

    def can_fast_delete(self, objs, from_field=None):
        """
        Determine if the objects in the given queryset-like or single object
        can be fast-deleted. This can be done if there are no cascades, no
        parents and no signal listeners for the object class.
        The 'from_field' tells where we are coming from - we need this to
        determine if the objects are in fact to be deleted. Allow also
        skipping parent -> child -> parent chain preventing fast delete of
        the child.
        """
        if from_field and from_field.remote_field.on_delete is not models.CASCADE:
            return False
        if hasattr(objs, "_meta"):
            model = objs._meta.model
        elif hasattr(objs, "model") and hasattr(objs, "_raw_delete"):
            model = objs.model
        else:
            return False
        # if self._has_signal_listeners(model):
        #     return False
        # The use of from_field comes from the need to avoid cascade back to
        # parent when parent delete is cascading to child.
        opts = model._meta
        return (
            all(
                link == from_field
                for link in opts.concrete_model._meta.parents.values()
            )
            and
            # Foreign keys pointing to this model.
            all(
                related.field.remote_field.on_delete is models.DO_NOTHING
                for related in get_candidate_relations_to_delete(opts)
            )
            and (
                # Something like generic foreign key.
                not any(
                    hasattr(field, "bulk_related_objects")
                    for field in opts.private_fields
                )
            )
        )

    def get_del_batches(self, objs, fields):
        """
        Return the objs in suitably sized batches for the used connection.
        """
        field_names = [field.name for field in fields]
        conn_batch_size = max(
            connections[self.using].ops.bulk_batch_size(field_names, objs), 1
        )
        if len(objs) > conn_batch_size:
            return [
                objs[i : i + conn_batch_size]
                for i in range(0, len(objs), conn_batch_size)
            ]
        else:
            return [objs]

    def collect(
        self,
        objs,
        source=None,
        nullable=False,
        collect_related=True,
        source_attr=None,
        reverse_dependency=False,
        keep_parents=False,
        fail_on_restricted=True,
    ):
        """
        Add 'objs' to the collection of objects to be deleted as well as all
        parent instances.  'objs' must be a homogeneous iterable collection of
        model instances (e.g. a QuerySet).  If 'collect_related' is True,
        related objects will be handled by their respective on_delete handler.
        If the call is the result of a cascade, 'source' should be the model
        that caused it and 'nullable' should be set to True, if the relation
        can be null.
        If 'reverse_dependency' is True, 'source' will be deleted before the
        current model, rather than after. (Needed for cascading to parent
        models, the one case in which the cascade follows the forwards
        direction of an FK rather than the reverse direction.)
        If 'keep_parents' is True, data of parent model's will be not deleted.
        If 'fail_on_restricted' is False, error won't be raised even if it's
        prohibited to delete such objects due to RESTRICT, that defers
        restricted object checking in recursive calls where the top-level call
        may need to collect more objects to determine whether restricted ones
        can be deleted.
        """
        new_objs = self.add(
            objs, source, nullable, reverse_dependency=reverse_dependency
        )
        if not new_objs:
            return

        # import ipdb; ipdb.sset_trace()
        model = new_objs[0].__class__

        for related in get_candidate_relations_to_delete(model._meta):
            collected_objs = []
            for obj in new_objs:
                val = getattr(obj, related.name)
                if val:
                    collected_objs.append(val)

            new_objs = self.collect(objs=collected_objs, source=model, reverse_dependency=False)

            continue


        # if not keep_parents:
        #     # Recursively collect concrete model's parent models, but not their
        #     # related objects. These will be found by meta.get_fields()
        #     concrete_model = model._meta.concrete_model
        #     for ptr in concrete_model._meta.parents.values():
        #         if ptr:
        #             parent_objs = [getattr(obj, ptr.name) for obj in new_objs]
        #             self.collect(
        #                 parent_objs,
        #                 source=model,
        #                 source_attr=ptr.remote_field.related_name,
        #                 collect_related=False,
        #                 reverse_dependency=True,
        #                 fail_on_restricted=False,
        #             )
        # if not collect_related:
        #     return

        # if keep_parents:
        #     parents = set(model._meta.get_parent_list())
        # model_fast_deletes = defaultdict(list)
        # protected_objects = defaultdict(list)
        # for related in get_candidate_relations_to_delete(model._meta):
        #     # Preserve parent reverse relationships if keep_parents=True.
        #     if keep_parents and related.model in parents:
        #         continue
        #     field = related.field
        #     if field.remote_field.on_delete == models.DO_NOTHING:
        #         continue
        #     related_model = related.related_model
        #     if self.can_fast_delete(related_model, from_field=field):
        #         model_fast_deletes[related_model].append(field)
        #         continue

            # batches = self.get_del_batches(new_objs, [field])
            # for batch in batches:
            #     sub_objs = self.related_objects(related_model, [field], batch)
            #     # Non-referenced fields can be deferred if no signal receivers
            #     # are connected for the related model as they'll never be
            #     # exposed to the user. Skip field deferring when some
            #     # relationships are select_related as interactions between both
            #     # features are hard to get right. This should only happen in
            #     # the rare cases where .related_objects is overridden anyway.
            #     if not (
            #         sub_objs.query.select_related
            #         #or self._has_signal_listeners(related_model)
            #     ):
            #         referenced_fields = set(
            #             chain.from_iterable(
            #                 (rf.attname for rf in rel.field.foreign_related_fields)
            #                 for rel in get_candidate_relations_to_delete(
            #                     related_model._meta
            #                 )
            #             )
            #         )
            #         sub_objs = sub_objs.only(*tuple(referenced_fields))
            #     if sub_objs:
            #         try:
            #             field.remote_field.on_delete(self, field, sub_objs, self.using)
            #         except ProtectedError as error:
            #             key = "'%s.%s'" % (field.model.__name__, field.name)
            #             protected_objects[key] += error.protected_objects
        # if protected_objects:
        #     raise ProtectedError(
        #         "Cannot delete some instances of model %r because they are "
        #         "referenced through protected foreign keys: %s."
        #         % (
        #             model.__name__,
        #             ", ".join(protected_objects),
        #         ),
        #         set(chain.from_iterable(protected_objects.values())),
        #     )
        # for related_model, related_fields in model_fast_deletes.items():
        #     batches = self.get_del_batches(new_objs, related_fields)
        #     for batch in batches:
        #         sub_objs = self.related_objects(related_model, related_fields, batch)
        #         self.fast_deletes.append(sub_objs)
        # for field in model._meta.private_fields:
        #     if hasattr(field, "bulk_related_objects"):
        #         # It's something like generic foreign key.
        #         sub_objs = field.bulk_related_objects(new_objs, self.using)
        #         self.collect(
        #             sub_objs, source=model, nullable=True, fail_on_restricted=False
        #         )

        # if fail_on_restricted:
        #     # Raise an error if collected restricted objects (RESTRICT) aren't
        #     # candidates for deletion also collected via CASCADE.
        #     for related_model, instances in self.data.items():
        #         self.clear_restricted_objects_from_set(related_model, instances)
        #     for qs in self.fast_deletes:
        #         self.clear_restricted_objects_from_queryset(qs.model, qs)
        #     if self.restricted_objects.values():
        #         restricted_objects = defaultdict(list)
        #         for related_model, fields in self.restricted_objects.items():
        #             for field, objs in fields.items():
        #                 if objs:
        #                     key = "'%s.%s'" % (related_model.__name__, field.name)
        #                     restricted_objects[key] += objs
        #         if restricted_objects:
        #             raise RestrictedError(
        #                 "Cannot delete some instances of model %r because "
        #                 "they are referenced through restricted foreign keys: "
        #                 "%s."
        #                 % (
        #                     model.__name__,
        #                     ", ".join(restricted_objects),
        #                 ),
        #                 set(chain.from_iterable(restricted_objects.values())),
        #             )

    def related_objects(self, related_model, related_fields, objs):
        """
        Get a QuerySet of the related model to objs via related fields.
        """
        predicate = models.Q(
            *((f"{related_field.name}__in", objs) for related_field in related_fields),
            _connector=models.Q.OR,
        )
        return related_model._base_manager.using(self.using).filter(predicate)

    def instances_with_model(self):
        for model, instances in self.data.items():
            for obj in instances:
                yield model, obj

    def sort(self):
        sorted_models = []
        concrete_models = set()
        models = list(self.data)
        # import ipdb; ipdb.sset_trace()
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
