import django.contrib.auth.hashers
from django.db import migrations, models


def backfill_usernames_and_roles(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    for user in User.objects.all():
        if user.email == 'admin@rcsportal.com' or user.role == 'admin':
            user.username = 'admin'
            user.role = 'superadmin'
            user.password = django.contrib.auth.hashers.make_password('admin')
            user.is_superuser = True
            user.is_staff = True
        elif user.email == 'reseller@acme-messaging.com':
            user.username = 'reseller'
            user.role = 'reseller'
            user.password = django.contrib.auth.hashers.make_password('reseller')
        elif user.email and '@' in user.email:
            base_name = user.email.split('@')[0]
            user.username = base_name
            user.password = django.contrib.auth.hashers.make_password(base_name)
        else:
            user.username = f"user_{user.id}"
            user.password = django.contrib.auth.hashers.make_password(f"user_{user.id}")
        user.save()


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
    ]

    operations = [
        # 1. Add username as nullable first
        migrations.AddField(
            model_name='user',
            name='username',
            field=models.CharField(blank=True, db_index=True, max_length=150, null=True, verbose_name='Username'),
        ),
        # 2. Make email nullable/optional
        migrations.AlterField(
            model_name='user',
            name='email',
            field=models.EmailField(blank=True, db_index=True, max_length=254, null=True, verbose_name='Email address'),
        ),
        # 3. Update role choices
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(choices=[('superadmin', 'Superadmin'), ('reseller', 'Reseller'), ('user', 'User')], db_index=True, default='user', max_length=20, verbose_name='Role'),
        ),
        # 4. Backfill existing records with username & superadmin credentials
        migrations.RunPython(backfill_usernames_and_roles, reverse_code=migrations.RunPython.noop),
        # 5. Make username non-nullable and unique
        migrations.AlterField(
            model_name='user',
            name='username',
            field=models.CharField(db_index=True, help_text='Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.', max_length=150, unique=True, verbose_name='Username'),
        ),
    ]
