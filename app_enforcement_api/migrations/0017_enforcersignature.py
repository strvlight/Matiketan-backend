from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('app_enforcement_api', '0016_ticketsignature'),
    ]

    operations = [
        migrations.CreateModel(
            name='EnforcerSignature',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('data', models.BinaryField()),
                ('content_type', models.CharField(default='image/png', max_length=50)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('ticket', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='enforcer_signature', to='app_enforcement_api.ticket')),
            ],
        ),
    ]
