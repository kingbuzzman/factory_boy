# Copyright: See the LICENSE file.

"""Helpers for testing django apps."""

import os.path

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import signals

try:
    from PIL import Image
except ImportError:
    Image = None


class StandardModel(models.Model):
    foo = models.CharField(max_length=20)


class NonIntegerPk(models.Model):
    foo = models.CharField(max_length=20, primary_key=True)
    bar = models.CharField(max_length=20, blank=True)


class MultifieldModel(models.Model):
    slug = models.SlugField(max_length=20, unique=True)
    text = models.TextField()


class MultifieldUniqueModel(models.Model):
    slug = models.SlugField(max_length=20, unique=True)
    text = models.CharField(max_length=20, unique=True)
    title = models.CharField(max_length=20, unique=True)


class AbstractBase(models.Model):
    foo = models.CharField(max_length=20)

    class Meta:
        abstract = True


class ConcreteSon(AbstractBase):
    pass


class AbstractSon(AbstractBase):
    class Meta:
        abstract = True


class ConcreteGrandSon(AbstractSon):
    pass


class StandardSon(StandardModel):
    pass


class PointedModel(models.Model):
    foo = models.CharField(max_length=20)


class PointerModel(models.Model):
    bar = models.CharField(max_length=20)
    pointed = models.OneToOneField(
        PointedModel,
        related_name='pointer',
        null=True,
        on_delete=models.CASCADE
    )


class WithDefaultValue(models.Model):
    foo = models.CharField(max_length=20, default='')


class WithPassword(models.Model):
    pw = models.CharField(max_length=128)


WITHFILE_UPLOAD_TO = 'django'
WITHFILE_UPLOAD_DIR = os.path.join(settings.MEDIA_ROOT, WITHFILE_UPLOAD_TO)


class WithFile(models.Model):
    afile = models.FileField(upload_to=WITHFILE_UPLOAD_TO)


if Image is not None:  # PIL is available

    class WithImage(models.Model):
        animage = models.ImageField(upload_to=WITHFILE_UPLOAD_TO)
        size = models.IntegerField(default=0)

else:
    class WithImage(models.Model):
        pass


class WithSignals(models.Model):
    foo = models.CharField(max_length=20)

    def __init__(self, post_save_signal_receiver=None):
        super().__init__()
        if post_save_signal_receiver:
            signals.post_save.connect(
                post_save_signal_receiver,
                sender=self.__class__,
            )


class CustomManager(models.Manager):

    def create(self, arg=None, **kwargs):
        return super().create(**kwargs)


class WithCustomManager(models.Model):

    foo = models.CharField(max_length=20)

    objects = CustomManager()


class AbstractWithCustomManager(models.Model):
    custom_objects = CustomManager()

    class Meta:
        abstract = True


class FromAbstractWithCustomManager(AbstractWithCustomManager):
    pass


class P(models.Model):
    pass


class R(models.Model):
    is_default = models.BooleanField(default=False)
    p = models.ForeignKey(P, models.CASCADE, null=True)


def get_default_r():
    return R.objects.get_or_create(is_default=True)[0].pk


class S(models.Model):
    r = models.ForeignKey(R, models.CASCADE)


class T(models.Model):
    s = models.ForeignKey(S, models.CASCADE)


class U(models.Model):
    t = models.ForeignKey(T, models.CASCADE)


class RChild(R):
    text = models.CharField(max_length=10)


class A(models.Model):
    p_o = models.OneToOneField('P', models.CASCADE, related_name="+")
    p_f = models.ForeignKey('P', models.CASCADE, related_name="+")
    p_m = models.ManyToManyField('P')


class AA(models.Model):
    a = models.OneToOneField(A, models.CASCADE)
    u = models.OneToOneField(U, models.CASCADE)
    p = models.OneToOneField(P, models.CASCADE)


class B(models.Model):
    protect = models.ForeignKey(R, models.PROTECT)


class M(models.Model):
    m2m = models.ManyToManyField(R, related_name="m_set")
    m2m_through = models.ManyToManyField(R, through="MR", related_name="m_through_set")
    m2m_through_null = models.ManyToManyField(
        R, through="MRNull", related_name="m_through_null_set"
    )


class MR(models.Model):
    m = models.ForeignKey(M, models.CASCADE)
    r = models.ForeignKey(R, models.CASCADE)


class MRNull(models.Model):
    m = models.ForeignKey(M, models.CASCADE)
    r = models.ForeignKey(R, models.SET_NULL, null=True)


class HiddenUser(models.Model):
    r = models.ForeignKey(R, models.CASCADE, related_name="+")


class HiddenUserProfile(models.Model):
    user = models.ForeignKey(HiddenUser, models.CASCADE)


class M2MTo(models.Model):
    pass


class M2MFrom(models.Model):
    m2m = models.ManyToManyField(M2MTo)


class DeleteTop(models.Model):
    b1 = GenericRelation("GenericB1")
    b2 = GenericRelation("GenericB2")


class B1(models.Model):
    delete_top = models.ForeignKey(DeleteTop, models.CASCADE)


class B2(models.Model):
    delete_top = models.ForeignKey(DeleteTop, models.CASCADE)


class GenericModel(models.Model):
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    generic_obj = GenericForeignKey("content_type", "object_id")
