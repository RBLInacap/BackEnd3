from django.core.management.base import BaseCommand
from sgr_app.services.aws_service import test_s3_connection, test_rds_connection, is_s3_enabled

class Command(BaseCommand):
    help = 'Diagnóstico y verificación de conectividad con servicios Cloud AWS (Amazon S3 y Amazon RDS)'

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("=== DIAGNÓSTICO DE SERVICIOS CLOUD AWS (MATRIZ SGR) ==="))
        
        # 1. Diagnóstico de Base de Datos (RDS / MySQL)
        self.stdout.write("\n1. Verificando Base de Datos (Amazon RDS / MySQL)...")
        rds_res = test_rds_connection()
        if rds_res['status'] == 'connected':
            self.stdout.write(self.style.SUCCESS(f"  [OK] {rds_res['message']} (Host: {rds_res['host']}, BD: {rds_res.get('database')})"))
        elif rds_res['status'] == 'local_sqlite':
            self.stdout.write(self.style.WARNING(f"  [AVISO] {rds_res['message']}"))
            self.stdout.write("  Para conectar a Amazon RDS, configure USE_MYSQL=True, DB_HOST=<endpoint-rds>, DB_NAME, DB_USER, DB_PASSWORD.")
        else:
            self.stdout.write(self.style.ERROR(f"  [ERROR] {rds_res['message']}"))

        # 2. Diagnóstico de Almacenamiento Multimedia (Amazon S3)
        self.stdout.write("\n2. Verificando Almacenamiento Multimedia (Amazon S3)...")
        s3_res = test_s3_connection()
        if s3_res['status'] == 'connected':
            self.stdout.write(self.style.SUCCESS(f"  [OK] {s3_res['message']}"))
        elif s3_res['status'] == 'disabled':
            self.stdout.write(self.style.WARNING(f"  [AVISO] {s3_res['message']}"))
            self.stdout.write("  Para activar Amazon S3 en producción AWS, configure USE_S3=True, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_STORAGE_BUCKET_NAME.")
        else:
            self.stdout.write(self.style.ERROR(f"  [ERROR] {s3_res['message']}"))

        self.stdout.write("\n" + "=" * 55)
