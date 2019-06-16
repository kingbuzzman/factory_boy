# -*- coding: utf-8 -*-
# Copyright: See the LICENSE file.


"""factory_boy extensions for use with the Django framework."""

from __future__ import absolute_import, unicode_literals

import contextlib
import functools
import logging
from collections import defaultdict

from .. import base

logger = logging.getLogger('factory.generate')


class mute_signals(object):
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

            signal.receivers = receivers
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


@contextlib.contextmanager
def suppress_autotime(model, fields):
    """
    Disable auto_now during a "with" block.

    This function is NOT thread-safe because it changes the model itself temporarily.
    This is ok because we're in a test and all tests run in one thread.
    """
    # Disable auto_now & auto_now_add for every field in this model that has them,
    # saving the original values first.
    original_values = defaultdict(dict)
    for field in (f for f in model._meta.local_fields if f.name in fields):
        if hasattr(field, 'auto_now'):
            original_values[field]['auto_now'] = field.auto_now
            field.auto_now = False
        if hasattr(field, 'auto_now_add'):
            original_values[field]['auto_now_add'] = field.auto_now_add
            field.auto_now_add = False

    try:
        yield  # Execute the logic in the "with" block.
    finally:
        # After the "with" block, put original values back.
        for field, attrs in original_values.items():
            for attr, value in attrs.items():
                setattr(field, attr, value)
