from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app_enforcement_api', '0015_enforcer_created_by'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='violator_signature',
            field=models.ImageField(blank=True, null=True, upload_to='signatures/'),
        ),
    ]
