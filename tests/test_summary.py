from django.core.management import call_command

from chamas.models import Chama, Contribution, Member


def test_seed_demo_is_idempotent_and_summary_matches(db, client):
    call_command("seed_demo")
    counts = (Member.objects.count(), Contribution.objects.count())
    call_command("seed_demo")
    assert (Member.objects.count(), Contribution.objects.count()) == counts
    assert counts[0] == 12

    token = client.post("/api/auth/token/", {"username": "demo", "password": "demo1234"}).json()["access"]
    chama = Chama.objects.get(short_code="UMOJA")
    resp = client.get(f"/api/chamas/{chama.pk}/summary/", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert resp.status_code == 200
    data = resp.json()

    by_name = {m["full_name"].split()[0]: m for m in data["members"]}
    in_arrears = {name for name, m in by_name.items() if m["arrears"] > 0}
    assert in_arrears == {"Lucy", "Agnes", "Mercy"}
    assert by_name["Lucy"]["arrears"] == 2000
    assert by_name["Mercy"]["arrears"] == 500
    assert by_name["Esther"]["credit"] == 2000
    assert by_name["Ruth"]["is_active"] is False and by_name["Ruth"]["expected"] == 0
    assert by_name["Caroline"]["periods_due"] == 3 and by_name["Caroline"]["arrears"] == 0
    assert data["members_in_arrears"] == 3
    assert data["period_expected"] == 11 * 1000
    assert data["total_collected_all_time"] > 0
