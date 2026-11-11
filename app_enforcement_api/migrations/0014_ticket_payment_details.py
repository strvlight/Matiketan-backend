from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app_enforcement_api", "0013_confirmeddetection_declineddetection_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="ticket",
            name="receipt_number",
            field=models.CharField(max_length=100, null=True, blank=True, unique=True),
        ),
        migrations.AddField(
            model_name="ticket",
            name="owner_name",
            field=models.CharField(max_length=255, null=True, blank=True),
        ),
        migrations.AddField(
            model_name="ticket",
            name="amount_paid",
            field=models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True),
        ),
        migrations.AddField(
            model_name="ticket",
            name="payment_method",
            field=models.CharField(max_length=50, null=True, blank=True),
        ),
        migrations.AddField(
            model_name="ticket",
            name="remarks",
            field=models.TextField(null=True, blank=True),
        ),
    ]
