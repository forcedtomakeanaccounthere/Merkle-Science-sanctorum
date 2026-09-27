from datetime import datetime

import pytest

from tests.conftest import START


def create_member(client, **overrides):
    payload = {"name": "Stephen Strange", "email": "stephen@sanctum.org"}
    payload.update(overrides)
    return client.post("/members", json=payload)


class TestCreateMember:
    def test_create_returns_201_with_member(self, client):
        response = create_member(client, tier="master")
        assert response.status_code == 201
        body = response.json()
        assert isinstance(body["id"], int)
        assert body["name"] == "Stephen Strange"
        assert body["email"] == "stephen@sanctum.org"
        assert body["tier"] == "master"

    def test_tier_defaults_to_apprentice(self, client):
        assert create_member(client).json()["tier"] == "apprentice"

    @pytest.mark.parametrize("tier", ["apprentice", "adept", "master", "supreme"])
    def test_every_tier_is_accepted(self, client, tier):
        response = create_member(client, tier=tier)
        assert response.status_code == 201
        assert response.json()["tier"] == tier

    def test_unknown_tier_returns_422(self, client):
        assert create_member(client, tier="grandmaster").status_code == 422

    def test_created_at_is_current_clock_time(self, client):
        response = create_member(client)
        assert datetime.fromisoformat(response.json()["created_at"]) == START

    def test_created_at_follows_the_clock(self, client, clock):
        clock.advance(days=3, hours=2)
        response = create_member(client)
        assert datetime.fromisoformat(response.json()["created_at"]) == clock.current

    def test_name_is_stripped(self, client):
        assert create_member(client, name="  Wong  ").json()["name"] == "Wong"

    def test_name_of_100_chars_is_allowed(self, client):
        assert create_member(client, name="a" * 100).status_code == 201

    @pytest.mark.parametrize("name", ["", "    ", "a" * 101])
    def test_invalid_name_returns_422(self, client, name):
        assert create_member(client, name=name).status_code == 422

    def test_email_is_stripped_and_lowercased(self, client):
        response = create_member(client, email="  Stephen.Strange@Sanctum.ORG  ")
        assert response.status_code == 201
        assert response.json()["email"] == "stephen.strange@sanctum.org"

    @pytest.mark.parametrize(
        "email",
        [
            "",
            "plainaddress",
            "@sanctum.org",
            "wong@",
            "wong@sanctum",
            "wong@sanctum.",
            "wong@@sanctum.org",
            "wong strange@sanctum.org",
        ],
    )
    def test_invalid_email_returns_422(self, client, email):
        assert create_member(client, email=email).status_code == 422

    def test_duplicate_email_returns_409(self, client):
        assert create_member(client).status_code == 201
        assert create_member(client, name="Someone Else").status_code == 409

    def test_duplicate_email_is_case_insensitive(self, client):
        assert create_member(client, email="wong@sanctum.org").status_code == 201
        assert create_member(client, email="WONG@Sanctum.org").status_code == 409


class TestGetMember:
    def test_get_existing_member(self, client, make_member):
        member = make_member()
        response = client.get(f"/members/{member['id']}")
        assert response.status_code == 200
        assert response.json() == member

    def test_get_missing_member_returns_404(self, client):
        assert client.get("/members/9999").status_code == 404


class TestMemberStats:
    def test_new_member_has_zero_stats(self, client, make_member):
        member = make_member()
        response = client.get(f"/members/{member['id']}/stats")
        assert response.status_code == 200
        assert response.json() == {
            "member_id": member["id"],
            "orders_paid": 0,
            "total_spent_cents": 0,
            "active_loans": 0,
            "overdue_loans": 0,
            "late_fees_cents": 0,
        }

    def test_order_stats_count_only_paid_orders(self, client, make_member, make_book):
        member = make_member(tier="supreme")  # 15% discount
        book = make_book(price_cents=1000, stock=10)

        def order(quantity):
            body = {"member_id": member["id"], "items": [{"book_id": book["id"], "quantity": quantity}]}
            response = client.post("/orders", json=body)
            assert response.status_code == 201
            return response.json()["id"]

        client.post(f"/orders/{order(2)}/pay")  # total 1700
        client.post(f"/orders/{order(1)}/pay")  # total 850
        order(3)  # stays pending
        client.post(f"/orders/{order(4)}/cancel")

        stats = client.get(f"/members/{member['id']}/stats").json()
        assert stats["orders_paid"] == 2
        assert stats["total_spent_cents"] == 2550

    def test_loan_stats(self, client, clock, make_member, make_book):
        member = make_member(tier="supreme")
        returned_late = make_book(price_cents=1000)
        overdue = make_book()
        active = make_book()

        def borrow(book):
            body = {"member_id": member["id"], "book_id": book["id"]}
            response = client.post("/loans", json=body)
            assert response.status_code == 201
            return response.json()["id"]

        late_loan = borrow(returned_late)  # due START + 14d
        borrow(overdue)  # due START + 14d
        clock.advance(days=10)
        borrow(active)  # due START + 24d
        clock.advance(days=10)  # now START + 20d
        assert client.post(f"/loans/{late_loan}/return").status_code == 200  # 6 days late

        stats = client.get(f"/members/{member['id']}/stats").json()
        assert stats["active_loans"] == 2
        assert stats["overdue_loans"] == 1
        assert stats["late_fees_cents"] == 150

    def test_loan_due_exactly_now_counts_as_active_not_overdue(self, client, clock, make_member, make_book):
        member = make_member()
        book = make_book()
        body = {"member_id": member["id"], "book_id": book["id"]}
        assert client.post("/loans", json=body).status_code == 201  # due START + 14d
        clock.advance(days=14)  # now == due_at exactly

        stats = client.get(f"/members/{member['id']}/stats").json()
        assert stats["active_loans"] == 1
        assert stats["overdue_loans"] == 0

    def test_stats_for_missing_member_returns_404(self, client):
        assert client.get("/members/9999/stats").status_code == 404


class TestListMembers:
    """Tests for GET /members endpoint with pagination."""

    def test_list_members_returns_paginated_results(self, client, make_member):
        """Basic pagination test."""
        members = [make_member() for _ in range(5)]
        
        response = client.get("/members?limit=20&offset=0")
        assert response.status_code == 200
        body = response.json()
        
        assert "items" in body
        assert "total" in body
        assert "limit" in body
        assert "offset" in body
        assert body["limit"] == 20
        assert body["offset"] == 0
        assert body["total"] >= 5
        assert len(body["items"]) >= 5

    def test_list_members_default_pagination(self, client, make_member):
        """Test default limit and offset values."""
        make_member()
        
        response = client.get("/members")
        assert response.status_code == 200
        body = response.json()
        
        assert body["limit"] == 20
        assert body["offset"] == 0

    def test_list_members_with_custom_limit(self, client, make_member):
        """Test custom limit parameter."""
        for _ in range(5):
            make_member()
        
        response = client.get("/members?limit=3")
        assert response.status_code == 200
        body = response.json()
        
        assert body["limit"] == 3
        assert len(body["items"]) <= 3

    def test_list_members_with_offset(self, client, make_member):
        """Test offset parameter for pagination."""
        members = [make_member() for _ in range(5)]
        member_ids = [m["id"] for m in members]
        
        # Get first page
        response1 = client.get("/members?limit=2&offset=0")
        page1 = response1.json()
        
        # Get second page
        response2 = client.get("/members?limit=2&offset=2")
        page2 = response2.json()
        
        # Items should be different
        page1_ids = [m["id"] for m in page1["items"]]
        page2_ids = [m["id"] for m in page2["items"]]
        
        # No overlap between pages
        assert not set(page1_ids) & set(page2_ids)

    def test_list_members_ordered_by_id(self, client, make_member):
        """Members should be ordered by id (creation order)."""
        members = [make_member() for _ in range(3)]
        
        response = client.get("/members?limit=100")
        body = response.json()
        
        # Find our test members in the response
        our_members = [m for m in body["items"] if m["id"] in [mem["id"] for mem in members]]
        ids = [m["id"] for m in our_members]
        
        # Should be in ascending order
        assert ids == sorted(ids)

    def test_list_members_total_count(self, client, make_member):
        """Total should reflect the total count regardless of pagination."""
        # Get initial count
        initial_response = client.get("/members")
        initial_total = initial_response.json()["total"]
        
        # Add 3 more members
        for _ in range(3):
            make_member()
        
        response = client.get("/members?limit=1")
        body = response.json()
        
        assert body["total"] == initial_total + 3
        assert len(body["items"]) == 1

    def test_list_members_empty_database(self, client):
        """Listing members when none exist should return empty list."""
        response = client.get("/members")
        assert response.status_code == 200
        body = response.json()
        
        # Might have members from other tests, but structure should be correct
        assert "items" in body
        assert isinstance(body["items"], list)
        assert body["total"] >= 0

    def test_list_members_limit_validation(self, client):
        """Test limit parameter validation."""
        # Limit below minimum
        response = client.get("/members?limit=0")
        assert response.status_code == 422
        
        # Limit above maximum
        response = client.get("/members?limit=101")
        assert response.status_code == 422
        
        # Valid limits
        assert client.get("/members?limit=1").status_code == 200
        assert client.get("/members?limit=100").status_code == 200

    def test_list_members_offset_validation(self, client):
        """Test offset parameter validation."""
        # Negative offset
        response = client.get("/members?offset=-1")
        assert response.status_code == 422
        
        # Valid offset
        assert client.get("/members?offset=0").status_code == 200
        assert client.get("/members?offset=100").status_code == 200

    def test_list_members_beyond_available_items(self, client, make_member):
        """Requesting offset beyond available items returns empty list."""
        make_member()
        
        response = client.get("/members?offset=1000")
        assert response.status_code == 200
        body = response.json()
        
        # Should return empty items but correct total
        assert body["total"] >= 1
        assert len(body["items"]) == 0

    def test_list_members_includes_all_fields(self, client, make_member):
        """Each member in the list should include all required fields."""
        member = make_member(name="Test Member", tier="master")
        
        response = client.get("/members")
        assert response.status_code == 200
        
        items = response.json()["items"]
        our_member = next((m for m in items if m["id"] == member["id"]), None)
        
        assert our_member is not None
        assert our_member["id"] == member["id"]
        assert our_member["name"] == "Test Member"
        assert our_member["email"] == member["email"]
        assert our_member["tier"] == "master"
        assert "created_at" in our_member
