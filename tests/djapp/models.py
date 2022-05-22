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
    text = models.CharField(max_length=20)


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
    pass


class A(models.Model):
    name = models.CharField(max_length=30)

    auto = models.ForeignKey(R, models.CASCADE, related_name="auto_set")
    auto_nullable = models.ForeignKey(
        R, models.CASCADE, null=True, related_name="auto_nullable_set"
    )
    setvalue = models.ForeignKey(R, models.SET(get_default_r), related_name="setvalue")
    setnull = models.ForeignKey(
        R, models.SET_NULL, null=True, related_name="setnull_set"
    )
    setdefault = models.ForeignKey(
        R, models.SET_DEFAULT, default=get_default_r, related_name="setdefault_set"
    )
    setdefault_none = models.ForeignKey(
        R,
        models.SET_DEFAULT,
        default=None,
        null=True,
        related_name="setnull_nullable_set",
    )
    cascade = models.ForeignKey(R, models.CASCADE, related_name="cascade_set")
    cascade_nullable = models.ForeignKey(
        R, models.CASCADE, null=True, related_name="cascade_nullable_set"
    )
    protect = models.ForeignKey(
        R, models.PROTECT, null=True, related_name="protect_set"
    )
    donothing = models.ForeignKey(
        R, models.DO_NOTHING, null=True, related_name="donothing_set"
    )
    child = models.ForeignKey(RChild, models.CASCADE, related_name="child")
    child_setnull = models.ForeignKey(
        RChild, models.SET_NULL, null=True, related_name="child_setnull"
    )
    cascade_p = models.ForeignKey(
        P, models.CASCADE, related_name="cascade_p_set", null=True
    )

    # A OneToOneField is just a ForeignKey unique=True, so we don't duplicate
    # all the tests; just one smoke test to ensure on_delete works for it as
    # well.
    o2o_setnull = models.ForeignKey(
        R, models.SET_NULL, null=True, related_name="o2o_nullable_set"
    )


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


class B3(models.Model):
    restrict = models.ForeignKey(R, models.CASCADE)


class DeleteBottom(models.Model):
    b1 = models.ForeignKey(B1, models.CASCADE)
    b2 = models.ForeignKey(B2, models.CASCADE)


class GenericB1(models.Model):
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    generic_delete_top = GenericForeignKey("content_type", "object_id")


class GenericB2(models.Model):
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    generic_delete_top = GenericForeignKey("content_type", "object_id")
    generic_delete_bottom = GenericRelation("GenericDeleteBottom")


class GenericDeleteBottom(models.Model):
    generic_b1 = models.ForeignKey(GenericB1, models.CASCADE)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    generic_b2 = GenericForeignKey()


class GenericDeleteBottomParent(models.Model):
    generic_delete_bottom = models.ForeignKey(
        GenericDeleteBottom, on_delete=models.CASCADE
    )
