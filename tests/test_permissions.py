from chamas.models import Chama, Contribution, Member


def test_member_cannot_record_cash(chama, plain_member, client_for):
    user, member = plain_member
    resp = client_for(user).post(
        f"/api/chamas/{chama.pk}/contributions/cash/",
        {"member_id": member.pk, "amount": 1000, "period": "2026-10", "method": "cash"},
        format="json",
    )
    assert resp.status_code == 403
    assert not Contribution.objects.exists()


def test_chair_cannot_record_cash_either(chama, client_for):
    chair = chama.members.get(role="chair")
    resp = client_for(chair.user).post(
        f"/api/chamas/{chama.pk}/contributions/cash/",
        {"member_id": chair.pk, "amount": 1000, "method": "cash"},
        format="json",
    )
    assert resp.status_code == 403


def test_treasurer_records_cash_as_confirmed(chama, treasurer, plain_member, client_for):
    _, member = plain_member
    resp = client_for(treasurer).post(
        f"/api/chamas/{chama.pk}/contributions/cash/",
        {"member_id": member.pk, "amount": 1000, "period": "2026-10-17", "method": "bank"},
        format="json",
    )
    assert resp.status_code == 201, resp.json()
    body = resp.json()
    assert body["status"] == "confirmed"
    assert body["method"] == "bank"
    assert body["period"] == "2026-10-01"  # snapped to the start of the month


def test_member_cannot_add_members(chama, plain_member, client_for):
    user, _ = plain_member
    resp = client_for(user).post(
        f"/api/chamas/{chama.pk}/members/",
        {"full_name": "Intruder", "phone": "0711000000"},
        format="json",
    )
    assert resp.status_code == 403
    assert not chama.members.filter(full_name="Intruder").exists()


def test_member_can_read_roster(chama, plain_member, client_for):
    user, _ = plain_member
    resp = client_for(user).get(f"/api/chamas/{chama.pk}/members/")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_official_adds_member_with_normalised_phone(chama, client_for):
    chair = chama.members.get(role="chair")
    resp = client_for(chair.user).post(
        f"/api/chamas/{chama.pk}/members/",
        {"full_name": "Janet Chebet", "phone": "+254 112 345 678"},
        format="json",
    )
    assert resp.status_code == 201, resp.json()
    assert resp.json()["phone"] == "254112345678"

    dup = client_for(chair.user).post(
        f"/api/chamas/{chama.pk}/members/", {"full_name": "Again", "phone": "0112345678"}, format="json"
    )
    assert dup.status_code == 400


def test_delete_deactivates_instead_of_deleting(chama, plain_member, client_for):
    _, member = plain_member
    chair = chama.members.get(role="chair")
    resp = client_for(chair.user).delete(f"/api/chamas/{chama.pk}/members/{member.pk}/")
    assert resp.status_code == 200
    member.refresh_from_db()
    assert member.is_active is False


def test_non_member_gets_404_on_another_chama(chama, make_user, client_for):
    outsider = make_user("outsider")
    client = client_for(outsider)
    for url in [
        f"/api/chamas/{chama.pk}/",
        f"/api/chamas/{chama.pk}/members/",
        f"/api/chamas/{chama.pk}/summary/",
    ]:
        assert client.get(url).status_code == 404, url
    assert client.post(f"/api/chamas/{chama.pk}/contributions/cash/", {}, format="json").status_code == 404
    assert client.get("/api/chamas/").json() == []


def test_non_member_gets_404_on_contribution_status(chama, make_user, client_for):
    contribution = Contribution.objects.create(
        member=chama.members.first(), amount=1000, period="2026-10-01", method="cash", status="confirmed"
    )
    resp = client_for(make_user("outsider")).get(f"/api/contributions/{contribution.pk}/status/")
    assert resp.status_code == 404


def test_deactivated_user_loses_access(chama, plain_member, client_for):
    user, member = plain_member
    Member.objects.filter(pk=member.pk).update(is_active=False)
    assert client_for(user).get(f"/api/chamas/{chama.pk}/").status_code == 404


def test_unauthenticated_is_401(chama, client):
    assert client.get("/api/chamas/").status_code == 401


def test_creator_becomes_chair(make_user, client_for):
    user = make_user("founder")
    resp = client_for(user).post(
        "/api/chamas/",
        {"name": "Tujenge", "short_code": "tujenge", "contribution_amount": 500,
         "frequency": "weekly", "chair_phone": "0722000000"},
        format="json",
    )
    assert resp.status_code == 201, resp.json()
    assert resp.json()["short_code"] == "TUJENGE"
    assert resp.json()["my_role"] == "chair"
    chama = Chama.objects.get(short_code="TUJENGE")
    assert chama.members.get(user=user).role == "chair"
    assert client_for(user).get("/api/chamas/").json()[0]["my_role"] == "chair"
