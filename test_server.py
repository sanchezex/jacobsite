import base64
import http.cookiejar
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

import server


class ServerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        server.DATA_DIR = Path(cls.tempdir.name)
        server.DB_PATH = server.DATA_DIR / "test.sqlite3"
        server.UPLOAD_DIR = server.DATA_DIR / "uploads"
        server.COOKIE_SECURE = False
        server.LOGIN_FAILURES.clear()
        server.initialize_database()
        with server.db_connect() as db:
            _, cls.admin_recovery_code = server.create_account(
                db, "admin@example.test", "AutoQuest Admin", "admin-password-123", role="admin"
            )
        cls.httpd = server.AutoQuestHTTPServer(("127.0.0.1", 0), server.AutoQuestHandler)
        cls.base_url = f"http://127.0.0.1:{cls.httpd.server_port}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.admin = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        response = cls.request("/api/auth/login", "POST", {"email": "admin@example.test", "password": "admin-password-123"}, opener=cls.admin)
        if response[0] != 200:
            raise RuntimeError("Could not sign in to the test admin account.")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls.tempdir.cleanup()

    @classmethod
    def request(cls, path, method="GET", body=None, opener=None, origin=None):
        headers = {}
        if origin is not False:
            headers["Origin"] = origin or cls.base_url
        payload = None if body is None else json.dumps(body).encode("utf-8")
        if payload is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            cls.base_url + path, data=payload, headers=headers, method=method
        )
        client = opener or urllib.request.build_opener()
        try:
            with client.open(request, timeout=5) as response:
                data = response.read()
                try:
                    result = json.loads(data)
                except json.JSONDecodeError:
                    result = data.decode("utf-8")
                return response.status, result
        except urllib.error.HTTPError as error:
            try:
                return error.code, json.loads(error.read())
            finally:
                error.close()

    def tearDown(self):
        with server.db_connect() as db:
            db.execute("DELETE FROM listings WHERE user_id IS NOT NULL")
            db.execute("DELETE FROM users WHERE role = 'tenant'")
        if server.UPLOAD_DIR.exists():
            for path in server.UPLOAD_DIR.iterdir():
                if path.is_file():
                    path.unlink()
        server.LOGIN_FAILURES.clear()

    @classmethod
    def make_listing(cls, title):
        jpeg = base64.b64encode(b"\xff\xd8\xff" + b"test image content").decode("ascii")
        return {
            "title": title,
            "make": "Toyota",
            "model": "Corolla",
            "year": 2020,
            "price": 1800000,
            "mileage": 40000,
            "location": "Nairobi",
            "bodyType": "sedan",
            "transmission": "Automatic",
            "fuel": "Petrol",
            "engine": "1.8L",
            "colour": "Silver",
            "description": "Test listing",
            "images": [f"data:image/jpeg;base64,{jpeg}"],
        }

    def create_tenant(self, email, plan="basic"):
        status, result = self.request(
            "/api/tenants",
            "POST",
            {
                "email": email,
                "name": "Test seller",
                "plan": plan,
                "permissions": {
                    "can_create_listings": True,
                    "can_manage_listings": True,
                    "can_manage_contact": True,
                },
            },
            opener=self.admin,
        )
        self.assertEqual(status, 201, result)
        seller = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        status, account = self.request(
            "/api/auth/login",
            "POST",
            {"email": email, "password": result["temporary_password"]},
            opener=seller,
        )
        self.assertEqual(status, 200, account)
        return seller, account["account"], result["temporary_password"]

    def test_public_inventory_and_authentication_boundary(self):
        status, _ = self.request("/nav.js")
        self.assertEqual(status, 200)
        status, result = self.request("/api/listings")
        self.assertEqual(status, 200)
        self.assertEqual(len(result["listings"]), 6)
        status, result = self.request("/api/account/listings")
        self.assertEqual(status, 401)
        status, _ = self.request(
            "/api/auth/login",
            "POST",
            {"email": "admin@example.test", "password": "admin-password-123"},
            origin="https://attacker.example",
        )
        self.assertEqual(status, 403)

    def test_api_rejects_invalid_plan_and_vehicle_type(self):
        status, invalid_plan = self.request(
            "/api/tenants",
            "POST",
            {"email": "bad-plan@example.test", "name": "Invalid Plan", "plan": []},
            opener=self.admin,
        )
        self.assertEqual(status, 400)
        status, _ = self.request(
            "/api/auth/recovery",
            "POST",
            {
                "email": "admin@example.test",
                "recoveryCode": self.admin_recovery_code,
                "newPassword": "abcde",
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("subscription plan", invalid_plan["error"])
        status, _ = self.request(
            "/api/auth/login",
            "POST",
            {"email": "admin@example.test", "password": "admin-password-123"},
            opener=self.admin,
        )
        self.assertEqual(status, 200)
        listing = self.make_listing("Bad body type")
        listing["bodyType"] = []
        status, invalid_vehicle = self.request("/api/listings", "POST", listing, opener=self.admin)
        self.assertEqual(status, 400)
        self.assertIn("vehicle details", invalid_vehicle["error"])
        status, overlong_password = self.request(
            "/api/auth/login",
            "POST",
            {"email": "admin@example.test", "password": "x" * 257},
            opener=self.admin,
        )
        self.assertEqual(status, 400)

    def test_admin_invites_start_without_permissions_unless_granted(self):
        status, invited = self.request(
            "/api/tenants",
            "POST",
            {
                "email": "least-privilege@example.test",
                "name": "Least Privilege Seller",
                "plan": "basic",
            },
            opener=self.admin,
        )
        self.assertEqual(status, 201, invited)
        seller = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        status, result = self.request(
            "/api/auth/login",
            "POST",
            {
                "email": "least-privilege@example.test",
                "password": invited["temporary_password"],
            },
            opener=seller,
        )
        self.assertEqual(status, 200, result)
        self.assertEqual(
            result["account"]["permissions"],
            {
                "can_create_listings": False,
                "can_manage_listings": False,
                "can_manage_contact": False,
            },
        )

    def test_tenant_activation_password_change_listing_and_owner_isolation(self):
        seller, tenant, temporary_password = self.create_tenant("seller-one@example.test")
        self.assertTrue(tenant["must_change_password"])
        status, _ = self.request(
            "/api/listings", "POST", self.make_listing("Pending car"), opener=seller
        )
        self.assertEqual(status, 403)

        status, _ = self.request(
            "/api/auth/password",
            "POST",
            {"currentPassword": temporary_password, "newPassword": "seller-password-456"},
            opener=seller,
        )
        self.assertEqual(status, 200)
        status, tenants = self.request("/api/tenants", opener=self.admin)
        tenant_id = next(item["id"] for item in tenants["tenants"] if item["email"] == "seller-one@example.test")
        status, _ = self.request(
            f"/api/tenants/{tenant_id}",
            "PATCH",
            {"subscription_status": "active"},
            opener=self.admin,
        )
        self.assertEqual(status, 200)
        status, created = self.request(
            "/api/listings", "POST", self.make_listing("Seller one car"), opener=seller
        )
        self.assertEqual(status, 201)
        status, public = self.request("/api/listings")
        self.assertIn(created["id"], {item["id"] for item in public["listings"]})
        other_seller, _, _ = self.create_tenant("seller-two@example.test")
        status, _ = self.request(
            f"/api/listings/{created['id']}",
            "DELETE",
            opener=other_seller,
        )
        self.assertEqual(status, 404)
        status, _ = self.request(f"/api/listings/{created['id']}", "DELETE", opener=seller)
        self.assertEqual(status, 200)
        status, public = self.request("/api/listings")
        self.assertNotIn(created["id"], {item["id"] for item in public["listings"]})

    def test_basic_plan_enforces_active_listing_limit(self):
        seller, tenant, temporary_password = self.create_tenant("seller-cap@example.test")
        status, tenants = self.request("/api/tenants", opener=self.admin)
        tenant_id = next(item["id"] for item in tenants["tenants"] if item["email"] == "seller-cap@example.test")
        self.request(
            f"/api/tenants/{tenant_id}",
            "PATCH",
            {"subscription_status": "active"},
            opener=self.admin,
        )
        status, _ = self.request(
            "/api/auth/password",
            "POST",
            {"currentPassword": temporary_password, "newPassword": "seller-cap-password"},
            opener=seller,
        )
        self.assertEqual(status, 200)
        with server.db_connect() as db:
            for index in range(3):
                db.execute(
                    """INSERT INTO listings
                       (id,user_id,title,make,model,year,price,mileage,location,body_type,
                        transmission,fuel,engine,colour,description,featured,status,images_json,created_at)
                       VALUES (?,?,'Existing','Toyota','Corolla',2020,1000000,10000,'Nairobi',
                               'sedan','Automatic','Petrol','1.8L','Silver','','0','active','[]',?)""",
                    (f"{index + 1:032x}", tenant["id"], server.utc_now().isoformat()),
                )
        status, result = self.request(
            "/api/listings", "POST", self.make_listing("Over limit"), opener=seller
        )
        self.assertEqual(status, 403)
        self.assertIn("allows up to 3", result["error"])

    def test_tenant_contact_is_used_for_public_enquiries(self):
        seller, _, temporary_password = self.create_tenant("seller-contact@example.test")
        status, _ = self.request(
            "/api/auth/password",
            "POST",
            {"currentPassword": temporary_password, "newPassword": "seller-contact-password"},
            opener=seller,
        )
        self.assertEqual(status, 200)
        self.request(
            "/api/account",
            "PATCH",
            {"whatsapp": "+254 712 345 678"},
            opener=seller,
        )
        status, tenants = self.request("/api/tenants", opener=self.admin)
        tenant_id = next(item["id"] for item in tenants["tenants"] if item["email"] == "seller-contact@example.test")
        self.request(
            f"/api/tenants/{tenant_id}",
            "PATCH",
            {"subscription_status": "active"},
            opener=self.admin,
        )
        status, created = self.request(
            "/api/listings", "POST", self.make_listing("Seller contact car"), opener=seller
        )
        self.assertEqual(status, 201)
        status, result = self.request("/api/listings")
        listing = next(item for item in result["listings"] if item["id"] == created["id"])
        self.assertEqual(listing["sellerWhatsApp"], "+254 712 345 678")

    def test_z_recovery_code_resets_password_and_rotates_once(self):
        status, result = self.request(
            "/api/auth/recovery",
            "POST",
            {
                "email": "admin@example.test",
                "recoveryCode": self.admin_recovery_code,
                "newPassword": "new-admin-password",
            },
        )
        self.assertEqual(status, 200)
        new_recovery_code = result["recovery_code"]
        status, error = self.request(
            "/api/auth/recovery",
            "POST",
            {
                "email": "admin@example.test",
                "recoveryCode": self.admin_recovery_code,
                "newPassword": "another-admin-password",
            },
        )
        self.assertEqual(status, 403)
        self.assertIn("incorrect", error["error"])
        status, next_recovery = self.request(
            "/api/auth/recovery",
            "POST",
            {
                "email": "admin@example.test",
                "recoveryCode": new_recovery_code,
                "newPassword": "another-admin-password",
            },
        )
        self.assertEqual(status, 200)
        self.assertNotEqual(new_recovery_code, next_recovery["recovery_code"])
        status, _ = self.request(
            "/api/auth/login",
            "POST",
            {"email": "admin@example.test", "password": "another-admin-password"},
        )
        self.assertEqual(status, 200)

    def test_self_registration_requires_admin_permissions(self):
        seller = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        status, result = self.request(
            "/api/auth/register",
            "POST",
            {
                "name": "New Showroom",
                "email": "new-showroom@example.test",
                "password": "seller-password-789",
                "plan": "basic",
            },
            opener=seller,
        )
        self.assertEqual(status, 201, result)
        self.assertEqual(result["account"]["subscription_status"], "pending")
        self.assertEqual(
            result["account"]["permissions"],
            {
                "can_create_listings": False,
                "can_manage_listings": False,
                "can_manage_contact": False,
            },
        )
        status, denied = self.request(
            "/api/listings", "POST", self.make_listing("Not yet allowed"), opener=seller
        )
        self.assertEqual(status, 403)
        self.assertIn("permission", denied["error"])

        _, tenants = self.request("/api/tenants", opener=self.admin)
        tenant_id = next(
            item["id"] for item in tenants["tenants"]
            if item["email"] == "new-showroom@example.test"
        )
        permissions = {
            "can_create_listings": True,
            "can_manage_listings": False,
            "can_manage_contact": False,
        }
        status, _ = self.request(
            f"/api/tenants/{tenant_id}",
            "PATCH",
            {"subscription_status": "active", "permissions": permissions},
            opener=self.admin,
        )
        self.assertEqual(status, 200)
        status, created = self.request(
            "/api/listings", "POST", self.make_listing("Permitted listing"), opener=seller
        )
        self.assertEqual(status, 201, created)
        status, denied = self.request(
            f"/api/listings/{created['id']}", "DELETE", opener=seller
        )
        self.assertEqual(status, 403)
        status, denied = self.request(
            "/api/account", "PATCH", {"whatsapp": "+254712345678"}, opener=seller
        )
        self.assertEqual(status, 403)


class FirstAdminSetupTests(unittest.TestCase):
    def test_first_admin_can_be_created_once_in_the_portal(self):
        tempdir = tempfile.TemporaryDirectory()
        old_data_dir, old_db_path, old_upload_dir = (
            server.DATA_DIR, server.DB_PATH, server.UPLOAD_DIR
        )
        old_cookie_secure = server.COOKIE_SECURE
        httpd = None
        thread = None
        try:
            server.DATA_DIR = Path(tempdir.name)
            server.DB_PATH = server.DATA_DIR / "setup-test.sqlite3"
            server.UPLOAD_DIR = server.DATA_DIR / "uploads"
            server.COOKIE_SECURE = False
            server.initialize_database()
            httpd = server.AutoQuestHTTPServer(
                ("127.0.0.1", 0), server.AutoQuestHandler
            )
            base_url = f"http://127.0.0.1:{httpd.server_port}"
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            client = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
            )

            def request(path, method="GET", body=None):
                headers = {"Origin": base_url}
                payload = None if body is None else json.dumps(body).encode()
                if payload is not None:
                    headers["Content-Type"] = "application/json"
                req = urllib.request.Request(
                    base_url + path, data=payload, headers=headers, method=method
                )
                try:
                    with client.open(req, timeout=5) as response:
                        return response.status, json.loads(response.read())
                except urllib.error.HTTPError as error:
                    try:
                        return error.code, json.loads(error.read())
                    finally:
                        error.close()

            status, result = request(
                "/api/auth/session"
            )
            self.assertEqual(status, 200)
            self.assertTrue(result["setup_required"])
            status, rejected_signup = request(
                "/api/auth/register",
                "POST",
                {
                    "name": "Early Seller",
                    "email": "early-seller@example.test",
                    "password": "seller-password-early",
                    "plan": "basic",
                },
            )
            self.assertEqual(status, 403)
            self.assertIn("administrator", rejected_signup["error"])
            status, created = request(
                "/api/auth/setup",
                "POST",
                {
                    "name": "First Admin",
                    "email": "first-admin@example.test",
                    "password": "first-admin-password",
                },
            )
            self.assertEqual(status, 201, created)
            self.assertEqual(created["account"]["role"], "admin")
            self.assertTrue(created["recovery_code"])
            status, session = request("/api/auth/session")
            self.assertEqual(status, 200)
            self.assertFalse(session["setup_required"])
            self.assertEqual(session["account"]["email"], "first-admin@example.test")
            status, rejected = request(
                "/api/auth/setup",
                "POST",
                {
                    "name": "Another Admin",
                    "email": "another-admin@example.test",
                    "password": "another-admin-password",
                },
            )
            self.assertEqual(status, 409)
            self.assertIn("already exists", rejected["error"])
        finally:
            if httpd:
                httpd.shutdown()
                httpd.server_close()
            if thread:
                thread.join(timeout=5)
            server.DATA_DIR, server.DB_PATH, server.UPLOAD_DIR = (
                old_data_dir, old_db_path, old_upload_dir
            )
            server.COOKIE_SECURE = old_cookie_secure
            tempdir.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
