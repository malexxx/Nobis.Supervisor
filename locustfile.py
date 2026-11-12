"""Scénarios de tests de charge et de montée en performance (Load Testing) avec Locust.

Exécution en ligne de commande (CLI sans interface) :
    locust -f locustfile.py --headless -u 20 -r 5 --run-time 30s --host http://localhost:8080

Exécution avec tableau de bord web interactif :
    locust -f locustfile.py --host http://localhost:8080
    (puis ouvrir http://localhost:8089 dans votre navigateur)
"""
from locust import HttpUser, between, task


class StoveDashboardUser(HttpUser):
    """Simule un utilisateur standard consultant l'interface Web ou l'application mobile PWA."""

    wait_time = between(1.0, 3.0)

    @task(10)
    def poll_latest_telemetry(self):
        """Lecture haute fréquence du dernier point de télémétrie et registres."""
        self.client.get("/api/latest", name="[GET] /api/latest")

    @task(4)
    def view_dashboard_charts(self):
        """Consultation des séries temporelles du tableau de bord (24h)."""
        self.client.get("/api/dashboard?range_key=24h", name="[GET] /api/dashboard?range_key=24h")

    @task(3)
    def check_silence_status(self):
        """Vérification de l'état du mode Silence intelligent."""
        self.client.get("/api/silence/status", name="[GET] /api/silence/status")

    @task(2)
    def check_health(self):
        """Vérification de santé de l'API (healthcheck)."""
        self.client.get("/health", name="[GET] /health")

    @task(1)
    def view_logs(self):
        """Consultation des journaux applicatifs récents."""
        self.client.get("/api/logs?severity=ALL", name="[GET] /api/logs")


class StoveOperatorUser(HttpUser):
    """Simule un utilisateur actif ou un automatisme domotique envoyant des ordres de commande."""

    wait_time = between(3.0, 7.0)

    @task(3)
    def consult_snapshots(self):
        """Consultation de la liste des sauvegardes/snapshots de registres."""
        self.client.get("/api/snapshots", name="[GET] /api/snapshots")

    @task(2)
    def trigger_temperature_adjustment(self):
        """Ajustement léger d'une consigne de température de test."""
        payload = {"type": "temp", "zone": "salon", "value": 21}
        self.client.post("/api/stove/set", json=payload, name="[POST] /api/stove/set")

    @task(1)
    def export_telemetry_csv(self):
        """Téléchargement de l'export CSV 3H."""
        self.client.get("/api/export-csv?range_key=3h", name="[GET] /api/export-csv?range_key=3h")
