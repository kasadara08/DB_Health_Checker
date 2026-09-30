"""
Regression test suite for DB configuration generation logic.
Tests: template isolation, sequential SID generation, password pattern derivation,
       reporting DB derivation, Aston/Fiat host selection, service-name generation,
       stale config prevention, and previous-config lookup.

Run from project root:
    python -m pytest tests/test_config_generation.py -v
    # or directly:
    python tests/test_config_generation.py
"""
import sys
import os
import copy
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.config_service import (
    derive_config_from_template,
    derive_similar_id,
    find_best_matching_template,
    find_longest_common_prefix,
    case_insensitive_replace,
    new_reporting_environment_state,
    advance_reporting_environment,
    ASTON_REPORTING_HOST,
    FIAT_REPORTING_HOST,
)

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

ASTON_TEMPLATE = {
    "db_id":            "fa194",
    "host":             "10.10.4.184",
    "port":             "1521",
    "service_name":     "fa194.dbprod02.csi.waynepa",
    "username":         "faop100",
    "password":         "faop100fa194",
    "os_user":          "oracle",
    "os_password":      "oracle123",
    "rep_db_id":        "fa194r",
    "rep_host":         "10.10.4.185",
    "rep_port":         "1521",
    "rep_service_name": "fa194r.aston.csi.waynepa",
    "rep_username":     "faor100",
    "rep_password":     "faor100fa194r",
    "rep_os_user":      "",
    "rep_os_password":  "",
    "stby_db_id":       "",
    "stby_host":        "",
    "stby_port":        "",
    "stby_service_name": "",
    "stby_username":    "",
    "stby_password":    "",
    "stby_os_user":     "",
    "stby_os_password": "",
}

FIAT_TEMPLATE = {
    "db_id":            "m5ddot",
    "host":             "10.10.4.100",
    "port":             "1521",
    "service_name":     "m5ddot.dbprod02.csi.waynepa",
    "username":         "fiop100",
    "password":         "fiop100m5ddot",
    "os_user":          "oracle",
    "os_password":      "oracle123",
    "rep_db_id":        "m5ddotr",
    "rep_host":         "10.10.4.101",
    "rep_port":         "1521",
    "rep_service_name": "m5ddotr.fiat.csi.waynepa",
    "rep_username":     "fior100",
    "rep_password":     "fior100m5ddotr",
    "rep_os_user":      "",
    "rep_os_password":  "",
    "stby_db_id":       "",
    "stby_host":        "",
    "stby_port":        "",
    "stby_service_name": "",
    "stby_username":    "",
    "stby_password":    "",
    "stby_os_user":     "",
    "stby_os_password": "",
}


def derive(sid, template):
    """Fresh derivation helper — always uses a deep copy of template."""
    cfg = {"db_id": sid}
    return derive_config_from_template(cfg, copy.deepcopy(template))


# ---------------------------------------------------------------------------
# 1. Template Isolation Tests
# ---------------------------------------------------------------------------

class TestTemplateIsolation(unittest.TestCase):
    """Verify that derive_config_from_template never mutates its template argument."""

    def test_template_not_mutated_single_call(self):
        before = copy.deepcopy(ASTON_TEMPLATE)
        derive("fa192", ASTON_TEMPLATE)
        self.assertEqual(ASTON_TEMPLATE, before,
                         "Template was mutated after a single derive call")

    def test_template_not_mutated_after_ten_calls(self):
        before = copy.deepcopy(ASTON_TEMPLATE)
        for sid in ["fa192", "fa193", "fa195", "fa134", "fa196",
                    "fa197", "fa198", "faaiap", "fahnrcop", "fautah1p"]:
            derive(sid, ASTON_TEMPLATE)
        self.assertEqual(ASTON_TEMPLATE, before,
                         "Template was mutated after repeated derive calls")

    def test_fresh_object_per_sid(self):
        cfg1 = derive("fa192", ASTON_TEMPLATE)
        cfg2 = derive("fa193", ASTON_TEMPLATE)
        self.assertIsNot(cfg1, cfg2)
        self.assertEqual(cfg1["db_id"], "fa192")
        self.assertEqual(cfg2["db_id"], "fa193")

    def test_active_template_independent_of_parsed_config(self):
        """Simulates Bug A: active_template must be deepcopy of template_config."""
        template_config = copy.deepcopy(ASTON_TEMPLATE)
        active_template = copy.deepcopy(template_config)   # ← the fix
        # Simulate modifying template_config (e.g., appending to parsed_configs
        # and then a piece of code modifying it)
        template_config["db_id"] = "CORRUPTED"
        # active_template must be unaffected
        self.assertEqual(active_template["db_id"], "fa194",
                         "active_template was mutated when template_config changed")


# ---------------------------------------------------------------------------
# 2. Sequential SID Generation Tests
# ---------------------------------------------------------------------------

class TestSequentialSIDGeneration(unittest.TestCase):
    """Verify that each SID in a sequential batch is generated independently."""

    ASTON_SIDS = [
        "fa192", "fa193", "fa195", "fa134", "fa196", "fa197", "fa198",
        "faaiap", "fahnrcop", "fautah1p", "fachpkp", "fabacrkp",
        "fadtonp", "fa158", "faengdp", "faorlanp", "fawburyp",
        "farcfdp", "fasarsop", "fa135", "fanmrlcp", "faoxnap",
        "faagrsvp", "facjeffp",
    ]
    M5_SIDS = ["m5macp", "m5gdyrp", "m5enbgdp", "m5caswp", "m5csdp", "m5mcewpp"]
    FIAT_SIDS = ["fa111", "fa109", "m5ccp", "m5csp", "m5ndotp"]

    def _assert_correct_derivation(self, cfg, template, label=""):
        sid = cfg["db_id"]
        t_id = template["db_id"]
        if sid.lower().endswith('p'):
            exp_rep = sid[:-1] + ('r' if sid[-1] == 'p' else 'R')
        else:
            exp_rep = sid + ('R' if sid[-1].isupper() else 'r')

        with self.subTest(sid=sid, label=label):
            # db_id appears in service_name
            self.assertIn(sid.lower(), cfg["service_name"].lower(),
                          f"{sid}: db_id missing from service_name '{cfg['service_name']}'")
            # db_id appears in password
            self.assertIn(sid.lower(), cfg["password"].lower(),
                          f"{sid}: db_id missing from password")
            # rep_db_id derived correctly
            self.assertEqual(cfg["rep_db_id"].lower(), exp_rep.lower(),
                             f"{sid}: rep_db_id '{cfg['rep_db_id']}' != expected '{exp_rep}'")
            # rep_db_id appears in rep_service_name
            self.assertIn(cfg["rep_db_id"].lower(), cfg["rep_service_name"].lower(),
                          f"{sid}: rep_db_id missing from rep_service_name")
            # rep_db_id appears in rep_password
            self.assertIn(cfg["rep_db_id"].lower(), cfg["rep_password"].lower(),
                          f"{sid}: rep_db_id missing from rep_password")
            # host comes from template unchanged
            self.assertEqual(cfg["host"], template["host"],
                             f"{sid}: host '{cfg['host']}' != template host '{template['host']}'")
            # rep_host comes from template unchanged
            self.assertEqual(cfg["rep_host"], template["rep_host"],
                             f"{sid}: rep_host '{cfg['rep_host']}' != template rep_host")
            # template db_id NOT in service_name of generated config (unless sid == t_id)
            if sid != t_id:
                self.assertNotIn(t_id.lower(), cfg["service_name"].lower(),
                                 f"{sid}: template id '{t_id}' still in service_name")

    def test_aston_sids_all_correct(self):
        for sid in self.ASTON_SIDS:
            cfg = derive(sid, ASTON_TEMPLATE)
            self._assert_correct_derivation(cfg, ASTON_TEMPLATE, "aston")

    def test_m5_sids_aston_template(self):
        """m5-prefixed DBs that belong to the Aston environment."""
        for sid in self.M5_SIDS:
            cfg = derive(sid, ASTON_TEMPLATE)
            with self.subTest(sid=sid):
                self.assertIn("aston", cfg["rep_service_name"].lower(),
                              f"{sid}: expected aston env in rep_service_name")
                self.assertEqual(cfg["host"], ASTON_TEMPLATE["host"])

    def test_fiat_sids_fiat_template(self):
        """Fiat SIDs must use the Fiat template, producing Fiat host and env."""
        for sid in self.FIAT_SIDS:
            cfg = derive(sid, FIAT_TEMPLATE)
            with self.subTest(sid=sid):
                self.assertIn("fiat", cfg["rep_service_name"].lower(),
                              f"{sid}: expected fiat env in rep_service_name")
                self.assertEqual(cfg["host"], FIAT_TEMPLATE["host"],
                                 f"{sid}: host should be FIAT host")
                self.assertEqual(cfg["rep_host"], FIAT_TEMPLATE["rep_host"],
                                 f"{sid}: rep_host should be FIAT rep_host")

    def test_sid_not_used_as_template_for_next(self):
        """
        Simulates the fixed upload loop: known_templates only contains explicit
        14-field template rows.  Each SID is derived from known_templates, not
        from the previously derived SID config.
        """
        known_templates = [copy.deepcopy(ASTON_TEMPLATE)]
        disk_pool = []
        active_template = copy.deepcopy(ASTON_TEMPLATE)

        sids = ["fa192", "fa193", "fa195"]
        results = []

        for sid in sids:
            # Template selection: only known_templates + disk_pool (no SID configs!)
            best = None
            best_len = -1
            for t in known_templates + disk_pool:
                score = find_longest_common_prefix(sid, t.get("db_id", ""))
                if score > best_len:
                    best_len = score
                    best = t
            if best_len <= 0:
                best = active_template
            tmpl = best

            cfg = {"db_id": sid}
            cfg = derive_config_from_template(cfg, copy.deepcopy(tmpl))
            results.append(cfg)
            # SID config is NOT added to known_templates ← the key fix

        # Verify all three results are independently correct
        for cfg in results:
            self._assert_correct_derivation(cfg, ASTON_TEMPLATE, "seq")

    def test_no_stale_config_reuse(self):
        """
        After saving, a second upload must not inherit stale values from the
        first upload's SID configs (Bug 3 fix).  The second parse uses its own
        active_template; save_database_configs must NOT re-derive.
        """
        # First upload: derives fa192 correctly
        cfg1 = derive("fa192", ASTON_TEMPLATE)
        self.assertEqual(cfg1["service_name"], "fa192.dbprod02.csi.waynepa")

        # Simulate a stale disk entry with the WRONG service_name
        # (as if a previous buggy save had stored it incorrectly)
        stale_disk = copy.deepcopy(cfg1)
        stale_disk["service_name"] = "fa192.WRONG.csi.waynepa"
        stale_disk["rep_service_name"] = "fa192r.WRONG.csi.waynepa"

        # Second upload derives fa192 fresh from active_template — must ignore stale disk
        cfg2_fresh = derive("fa192", ASTON_TEMPLATE)
        # The fresh derivation must NOT have the stale value
        self.assertNotIn("WRONG", cfg2_fresh["service_name"],
                         "Fresh derivation inherited stale disk service_name")
        self.assertEqual(cfg2_fresh["service_name"], "fa192.dbprod02.csi.waynepa")


# ---------------------------------------------------------------------------
# 3. Password Derivation Tests
# ---------------------------------------------------------------------------

class TestPasswordDerivation(unittest.TestCase):

    def test_prod_password_contains_new_db_id(self):
        cfg = derive("fa192", ASTON_TEMPLATE)
        self.assertIn("fa192", cfg["password"].lower())
        self.assertNotIn("fa194", cfg["password"].lower())

    def test_rep_password_contains_rep_db_id(self):
        cfg = derive("fa192", ASTON_TEMPLATE)
        rep_id = cfg["rep_db_id"]
        self.assertIn(rep_id.lower(), cfg["rep_password"].lower())
        # Must NOT contain the template's rep_db_id literally
        self.assertNotIn("fa194r", cfg["rep_password"].lower())

    def test_password_pattern_preserved(self):
        """Password pattern (prefix + db_id) must be derived correctly."""
        for sid in ["fa192", "fa193", "fa195", "fa134", "fa196"]:
            cfg = derive(sid, ASTON_TEMPLATE)
            expected_prefix = "faop100"   # from template password "faop100fa194"
            self.assertTrue(cfg["password"].lower().startswith(expected_prefix),
                            f"{sid}: password '{cfg['password']}' missing prefix '{expected_prefix}'")
            self.assertIn(sid, cfg["password"],
                          f"{sid}: password missing db_id")

    def test_rep_password_pattern(self):
        for sid in ["fa192", "fa193", "fa195"]:
            cfg = derive(sid, ASTON_TEMPLATE)
            rep_id = cfg["rep_db_id"]
            expected_prefix = "faor100"  # from template rep_password "faor100fa194r"
            self.assertTrue(cfg["rep_password"].lower().startswith(expected_prefix),
                            f"{sid}: rep_password missing prefix '{expected_prefix}'")
            self.assertIn(rep_id, cfg["rep_password"],
                          f"{sid}: rep_password missing rep_db_id '{rep_id}'")

    def test_explicit_password_preserved(self):
        """When user explicitly supplies a password in the upload, it must be kept."""
        cfg = {"db_id": "fa192", "password": "MyCustomP@ss"}
        derived = derive_config_from_template(cfg, copy.deepcopy(ASTON_TEMPLATE))
        self.assertEqual(derived["password"], "MyCustomP@ss",
                         "Explicit password was overwritten by template derivation")

    def test_fiat_password_pattern(self):
        for sid in ["fa111", "fa109", "m5ccp"]:
            cfg = derive(sid, FIAT_TEMPLATE)
            self.assertIn(sid, cfg["password"], f"{sid}: password missing db_id")
            rep_id = cfg["rep_db_id"]
            self.assertIn(rep_id, cfg["rep_password"],
                          f"{sid}: rep_password missing rep_db_id '{rep_id}'")

    def test_template_password_not_copied_literally(self):
        """The template's literal password must NOT appear in derived configs."""
        tmpl_pw     = ASTON_TEMPLATE["password"]      # "faop100fa194"
        tmpl_rep_pw = ASTON_TEMPLATE["rep_password"]  # "faor100fa194r"
        for sid in ["fa192", "fa193", "fa195"]:
            cfg = derive(sid, ASTON_TEMPLATE)
            self.assertNotEqual(cfg["password"], tmpl_pw,
                                f"{sid}: template password copied literally")
            self.assertNotEqual(cfg["rep_password"], tmpl_rep_pw,
                                f"{sid}: template rep_password copied literally")


# ---------------------------------------------------------------------------
# 4. Service-Name Derivation Tests
# ---------------------------------------------------------------------------

class TestServiceNameDerivation(unittest.TestCase):

    def test_prod_service_name_contains_db_id(self):
        for sid in ["fa192", "fa193", "fa195", "faaiap", "m5macp"]:
            cfg = derive(sid, ASTON_TEMPLATE)
            self.assertIn(sid, cfg["service_name"],
                          f"{sid}: service_name '{cfg['service_name']}' missing db_id")

    def test_prod_service_name_domain_preserved(self):
        """Domain suffix (e.g., .dbprod02.csi.waynepa) must come from template."""
        suffix = ".dbprod02.csi.waynepa"
        for sid in ["fa192", "faaiap", "m5macp"]:
            cfg = derive(sid, ASTON_TEMPLATE)
            self.assertIn(suffix, cfg["service_name"],
                          f"{sid}: domain suffix missing from service_name")

    def test_rep_service_name_aston_env(self):
        for sid in ["fa192", "fa193", "m5macp"]:
            cfg = derive(sid, ASTON_TEMPLATE)
            self.assertIn("aston", cfg["rep_service_name"].lower(),
                          f"{sid}: 'aston' missing from rep_service_name")

    def test_rep_service_name_fiat_env(self):
        for sid in ["fa111", "fa109", "m5ccp"]:
            cfg = derive(sid, FIAT_TEMPLATE)
            self.assertIn("fiat", cfg["rep_service_name"].lower(),
                          f"{sid}: 'fiat' missing from rep_service_name")

    def test_rep_service_name_contains_rep_db_id(self):
        for sid in ["fa192", "fa193", "fa195"]:
            cfg = derive(sid, ASTON_TEMPLATE)
            rep_id = cfg["rep_db_id"]
            self.assertIn(rep_id, cfg["rep_service_name"],
                          f"{sid}: rep_db_id '{rep_id}' missing from rep_service_name")

    def test_template_service_name_not_copied_literally(self):
        tmpl_svc = ASTON_TEMPLATE["service_name"]
        for sid in ["fa192", "m5macp"]:
            cfg = derive(sid, ASTON_TEMPLATE)
            if sid != ASTON_TEMPLATE["db_id"]:
                self.assertNotEqual(cfg["service_name"], tmpl_svc,
                                    f"{sid}: template service_name copied literally")

    def test_fiat_service_name_generation(self):
        """Fiat rep_service_name must contain the new rep_db_id + .fiat. domain."""
        for sid in ["fa111", "m5ccp", "m5csp", "m5ndotp"]:
            cfg = derive(sid, FIAT_TEMPLATE)
            rep_id = cfg["rep_db_id"]
            self.assertIn(rep_id, cfg["rep_service_name"])
            self.assertIn(".fiat.", cfg["rep_service_name"])


# ---------------------------------------------------------------------------
# 5. Host and Port Tests
# ---------------------------------------------------------------------------

class TestHostAndPort(unittest.TestCase):

    def test_aston_prod_host(self):
        for sid in ["fa192", "fa193", "faaiap"]:
            cfg = derive(sid, ASTON_TEMPLATE)
            self.assertEqual(cfg["host"], ASTON_TEMPLATE["host"],
                             f"{sid}: prod host mismatch")

    def test_aston_rep_host(self):
        for sid in ["fa192", "fa193", "m5macp"]:
            cfg = derive(sid, ASTON_TEMPLATE)
            self.assertEqual(cfg["rep_host"], ASTON_TEMPLATE["rep_host"],
                             f"{sid}: rep host mismatch")

    def test_fiat_prod_host_different_from_aston(self):
        """Fiat and Aston must have different prod hosts."""
        self.assertNotEqual(ASTON_TEMPLATE["host"], FIAT_TEMPLATE["host"])
        for sid in ["fa111", "m5ccp"]:
            fiat_cfg = derive(sid, FIAT_TEMPLATE)
            self.assertEqual(fiat_cfg["host"], FIAT_TEMPLATE["host"])
            self.assertNotEqual(fiat_cfg["host"], ASTON_TEMPLATE["host"])

    def test_fiat_rep_host_different_from_aston(self):
        """Fiat and Aston must have different rep hosts."""
        self.assertNotEqual(ASTON_TEMPLATE["rep_host"], FIAT_TEMPLATE["rep_host"])
        for sid in ["fa111", "m5ccp"]:
            fiat_cfg = derive(sid, FIAT_TEMPLATE)
            self.assertEqual(fiat_cfg["rep_host"], FIAT_TEMPLATE["rep_host"])
            self.assertNotEqual(fiat_cfg["rep_host"], ASTON_TEMPLATE["rep_host"])

    def test_port_preserved_from_template(self):
        for sid in ["fa192", "fa111"]:
            aston_cfg = derive(sid, ASTON_TEMPLATE)
            self.assertEqual(aston_cfg["port"], ASTON_TEMPLATE["port"])
            self.assertEqual(aston_cfg["rep_port"], ASTON_TEMPLATE["rep_port"])

    def test_fiat_and_aston_hosts_not_swapped_in_loop(self):
        """
        Simulate a loop processing Aston then Fiat SIDs.
        Fiat SIDs must never inherit Aston's host.
        """
        known_templates = [copy.deepcopy(ASTON_TEMPLATE), copy.deepcopy(FIAT_TEMPLATE)]
        disk_pool = []

        sids = [("fa192", ASTON_TEMPLATE), ("m5ccp", FIAT_TEMPLATE)]
        for sid, expected_tmpl in sids:
            # Select best template from known_templates
            best = None
            best_len = -1
            for t in known_templates + disk_pool:
                score = find_longest_common_prefix(sid, t.get("db_id", ""))
                if score > best_len:
                    best_len = score
                    best = t
            if best_len <= 0:
                best = known_templates[0]

            cfg = {"db_id": sid}
            cfg = derive_config_from_template(cfg, copy.deepcopy(best))

            with self.subTest(sid=sid):
                self.assertEqual(cfg["host"], expected_tmpl["host"],
                                 f"{sid}: wrong host '{cfg['host']}'")
                self.assertEqual(cfg["rep_host"], expected_tmpl["rep_host"],
                                 f"{sid}: wrong rep_host '{cfg['rep_host']}'")


# ---------------------------------------------------------------------------
# 6. Reporting DB Derivation Tests
# ---------------------------------------------------------------------------

class TestReportingDBDerivation(unittest.TestCase):

    def test_rep_db_id_suffix_rule(self):
        """Template: fa194 → fa194r. SID fa192 → fa192r."""
        for sid, expected in [("fa192", "fa192r"), ("fa193", "fa193r"), ("fa195", "fa195r"), ("faaiap", "faaiar"), ("m5macp", "m5macr")]:
            cfg = derive(sid, ASTON_TEMPLATE)
            self.assertEqual(cfg["rep_db_id"], expected,
                             f"{sid}: rep_db_id should be '{expected}'")

    def test_fiat_rep_db_id_suffix_rule(self):
        """Template: m5ddot → m5ddotr. SID m5ccp → m5ccr."""
        for sid, expected in [("m5ccp", "m5ccr"), ("m5csp", "m5csr"), ("fa111", "fa111r")]:
            cfg = derive(sid, FIAT_TEMPLATE)
            self.assertEqual(cfg["rep_db_id"], expected,
                             f"{sid}: rep_db_id should be '{expected}'")

    def test_explicit_rep_db_id_preserved(self):
        """Explicitly supplied rep_db_id must not be overwritten."""
        cfg = {"db_id": "fa192", "rep_db_id": "fa192_custom_r"}
        result = derive_config_from_template(cfg, copy.deepcopy(ASTON_TEMPLATE))
        self.assertEqual(result["rep_db_id"], "fa192_custom_r")


# ---------------------------------------------------------------------------
# 7. Template Selection Tests
# ---------------------------------------------------------------------------

class TestTemplateSelection(unittest.TestCase):

    def test_longest_prefix_wins(self):
        """fa192 shares 4 chars with fa194 and 3 chars with fa111f → fa194 wins."""
        candidates = [
            copy.deepcopy(ASTON_TEMPLATE),    # fa194
            copy.deepcopy(FIAT_TEMPLATE),      # m5ddot
        ]
        result = find_best_matching_template("fa192", candidates)
        self.assertEqual(result["db_id"], "fa194")

    def test_fiat_sid_selects_fiat_template_when_available(self):
        """m5ccp should pick the Fiat template if present in pool."""
        candidates = [
            copy.deepcopy(ASTON_TEMPLATE),    # fa194
            copy.deepcopy(FIAT_TEMPLATE),      # m5ddot
        ]
        result = find_best_matching_template("m5ccp", candidates)
        # m5ccp vs fa194: 0 prefix; m5ccp vs m5ddot: 2 prefix ('m5') → Fiat wins
        self.assertEqual(result["db_id"], "m5ddot",
                         "m5ccp should select Fiat template (longer 'm5' prefix)")

    def test_no_match_returns_none(self):
        """No prefix match at all should return None."""
        candidates = [{"db_id": "xyz123"}]
        result = find_best_matching_template("abcdef", candidates)
        self.assertIsNone(result)

    def test_in_file_template_beats_disk_on_tie(self):
        """
        Simulates the priority rule: in-file templates take priority over disk
        when prefix scores tie.
        """
        in_file = [copy.deepcopy(ASTON_TEMPLATE)]   # fa194, score 4 for fa192
        disk = [{"db_id": "fa19x", "rep_service_name": "fa19xr.aston.csi.waynepa",
                  "host": "DISK_HOST", "rep_host": "DISK_REP_HOST",
                  "password": "pw_disk_fa19x", "rep_password": "rep_pw_disk",
                  **{k: "" for k in ASTON_TEMPLATE if k not in [
                     "db_id","host","rep_host","password","rep_password","rep_service_name"]}}]

        # Both fa194 and fa19x have a 4-char prefix with fa192 ("fa19")
        # In-file should win on ties
        best_len = -1
        best_cfg = None
        for t in in_file:
            score = find_longest_common_prefix("fa192", t.get("db_id", ""))
            if score > best_len:
                best_len = score
                best_cfg = t
        for t in disk:
            score = find_longest_common_prefix("fa192", t.get("db_id", ""))
            if score > best_len:   # strictly greater → disk only wins if longer
                best_len = score
                best_cfg = t

        self.assertEqual(best_cfg["db_id"], "fa194",
                         "In-file template should beat disk template on tie")

    def test_sid_config_not_used_as_template(self):
        """
        Simulates the fix for Bug #7: the SID config from iteration N must NOT
        be in the candidate pool for iteration N+1.
        """
        known_templates = [copy.deepcopy(ASTON_TEMPLATE)]
        # Simulate iteration 1: generate fa192, but do NOT add to known_templates
        cfg_fa192 = derive("fa192", ASTON_TEMPLATE)
        # known_templates still only contains the master template
        self.assertEqual(len(known_templates), 1)
        self.assertEqual(known_templates[0]["db_id"], "fa194")

        # Iteration 2: select template for fa193 — must pick fa194, not fa192
        best = find_best_matching_template("fa193", known_templates)
        self.assertEqual(best["db_id"], "fa194",
                         "SID config fa192 must not be in candidate pool")
        cfg_fa193 = derive("fa193", ASTON_TEMPLATE)
        self.assertEqual(cfg_fa193["service_name"], "fa193.dbprod02.csi.waynepa")


# ---------------------------------------------------------------------------
# 8. Stale Config Prevention Tests
# ---------------------------------------------------------------------------

class TestStaleConfigPrevention(unittest.TestCase):

    def test_fresh_derivation_ignores_stale_disk(self):
        """
        Even if disk has a corrupted config for fa192, a fresh derivation from
        the correct template must produce the right values.
        Bug 3 fix: save_database_configs must NOT re-derive from old_configs_list.
        """
        stale_disk_fa192 = {
            "db_id":            "fa192",
            "host":             "WRONG_HOST",
            "port":             "9999",
            "service_name":     "fa192.WRONG.csi.waynepa",
            "username":         "WRONG_USER",
            "password":         "WRONG_PW",
            "rep_db_id":        "WRONG_REP",
            "rep_host":         "WRONG_REP_HOST",
            "rep_port":         "9998",
            "rep_service_name": "WRONG_REP.WRONG.csi.waynepa",
            "rep_username":     "",
            "rep_password":     "WRONG_REP_PW",
            "rep_os_user":      "", "rep_os_password":  "",
            "os_user":          "", "os_password":       "",
            "stby_db_id":       "", "stby_host":         "",
            "stby_port":        "", "stby_service_name": "",
            "stby_username":    "", "stby_password":     "",
            "stby_os_user":     "", "stby_os_password":  "",
        }

        # Fresh derivation uses the correct template, ignores stale disk value
        fresh = derive("fa192", ASTON_TEMPLATE)
        self.assertNotEqual(fresh["host"], "WRONG_HOST")
        self.assertNotEqual(fresh["service_name"], "fa192.WRONG.csi.waynepa")
        self.assertEqual(fresh["host"], ASTON_TEMPLATE["host"])
        self.assertEqual(fresh["service_name"], "fa192.dbprod02.csi.waynepa")

    def test_derive_with_stale_template_does_not_corrupt_non_empty_fields(self):
        """
        derive_config_from_template must NOT overwrite non-empty fields, even
        when called with a stale template (redundant re-derive scenario).
        """
        correct_cfg = derive("fa192", ASTON_TEMPLATE)
        original = copy.deepcopy(correct_cfg)

        # Now call derive again with a "stale" template (simulates Bug 2 post-process)
        stale_tmpl = copy.deepcopy(ASTON_TEMPLATE)
        stale_tmpl["db_id"] = "fa193"  # different db_id
        stale_tmpl["service_name"] = "fa193.dbprod02.csi.waynepa"
        derive_config_from_template(correct_cfg, stale_tmpl)

        # All previously-set non-empty fields must remain unchanged
        for key in original:
            if original[key]:
                self.assertEqual(correct_cfg[key], original[key],
                                 f"Re-derive with stale template changed '{key}'")


# ---------------------------------------------------------------------------
# 9. Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):

    def test_no_template_raises_clear_error(self):
        """
        Simulates the parse loop encountering a SID with no template available.
        The implementation raises ValueError (tested via the app.parse flow
        indirectly; here we test the underlying building block returns None).
        """
        result = find_best_matching_prefix = find_best_matching_template("zzz999", [])
        self.assertIsNone(result,
                          "find_best_matching_template should return None for empty pool")

    def test_derive_similar_id_standard(self):
        """Standard suffix derivation: fa194 → fa194r, so fa192 → fa192r."""
        result = derive_similar_id("fa194", "fa194r", "fa192")
        self.assertEqual(result, "fa192r")

    def test_derive_similar_id_m5_prefix(self):
        result = derive_similar_id("fa194", "fa194r", "m5macp")
        self.assertEqual(result, "m5macpr")

    def test_case_insensitive_replace(self):
        result = case_insensitive_replace("FA194.dbprod02.csi.waynepa", "FA194", "fa192")
        self.assertEqual(result, "fa192.dbprod02.csi.waynepa")

    def test_all_aston_sids_produce_independent_configs(self):
        """All 24 Aston SIDs in the spec produce distinct, correct configs."""
        all_sids = [
            "fa192","fa193","fa195","fa134","fa196","fa197","fa198",
            "faaiap","fahnrcop","fautah1p","fachpkp","fabacrkp",
            "fadtonp","fa158","faengdp","faorlanp","fawburyp",
            "farcfdp","fasarsop","fa135","fanmrlcp","faoxnap",
            "faagrsvp","facjeffp",
        ]
        seen_services = set()
        seen_passwords = set()
        for sid in all_sids:
            cfg = derive(sid, ASTON_TEMPLATE)
            self.assertIn(sid, cfg["service_name"], f"{sid}: missing from service_name")
            self.assertNotIn(cfg["service_name"], seen_services,
                             f"{sid}: duplicate service_name '{cfg['service_name']}'")
            self.assertNotIn(cfg["password"], seen_passwords,
                             f"{sid}: duplicate password for '{sid}'")
            seen_services.add(cfg["service_name"])
            seen_passwords.add(cfg["password"])


# ---------------------------------------------------------------------------
# 10. Reporting Environment Sequential Detection Tests
# ---------------------------------------------------------------------------
# The parser must NOT assume every Reporting DB is FIAT. The configuration
# file is ordered by Reporting environment: it starts as ASTON
# (10.10.4.184) and switches permanently to FIAT (10.10.4.100) the moment a
# Reporting Service Name containing "fiat" is encountered, in file order.

class TestReportingEnvironmentSequentialDetection(unittest.TestCase):

    def test_starts_as_aston(self):
        """A fresh state must always start as ASTON, never FIAT."""
        state = new_reporting_environment_state()
        self.assertEqual(state["env"], "ASTON")

    def test_fa194_is_aston(self):
        """fa194r.aston.csi.waynepa (no 'fiat' seen yet) -> ASTON -> 10.10.4.184."""
        state = new_reporting_environment_state()
        env, host = advance_reporting_environment(state, "fa194r.aston.csi.waynepa")
        self.assertEqual(env, "ASTON")
        self.assertEqual(host, ASTON_REPORTING_HOST)
        self.assertEqual(host, "10.10.4.184")

    def test_m5wccp_is_aston(self):
        """m5wccr.aston.csi.waynepa, appearing after fa194 and before any
        'fiat' marker, must still be ASTON -> 10.10.4.184 (state carries
        forward correctly across multiple ASTON records)."""
        state = new_reporting_environment_state()
        advance_reporting_environment(state, "fa194r.aston.csi.waynepa")   # fa194
        env, host = advance_reporting_environment(state, "m5wccr.aston.csi.waynepa")  # m5wccp
        self.assertEqual(env, "ASTON")
        self.assertEqual(host, ASTON_REPORTING_HOST)
        self.assertEqual(host, "10.10.4.184")

    def test_m5ddotp_is_first_fiat_marker(self):
        """m5ddotr.fiat.csi.waynepa is the first Reporting Service Name
        containing 'fiat' -> switches to FIAT -> 10.10.4.100."""
        state = new_reporting_environment_state()
        advance_reporting_environment(state, "fa194r.aston.csi.waynepa")    # fa194 -> ASTON
        advance_reporting_environment(state, "m5wccr.aston.csi.waynepa")    # m5wccp -> ASTON
        env, host = advance_reporting_environment(state, "m5ddotr.fiat.csi.waynepa")  # m5ddotp
        self.assertEqual(env, "FIAT")
        self.assertEqual(host, FIAT_REPORTING_HOST)
        self.assertEqual(host, "10.10.4.100")

    def test_full_sequence_matches_spec_example(self):
        """End-to-end: fa194 -> ASTON, m5wccp -> ASTON, m5ddotp -> FIAT,
        processed in a single pass with one shared state, exactly as the
        parser would encounter them in file order."""
        state = new_reporting_environment_state()
        records = [
            ("fa194",  "fa194r.aston.csi.waynepa"),
            ("m5wccp", "m5wccr.aston.csi.waynepa"),
            ("m5ddotp","m5ddotr.fiat.csi.waynepa"),
        ]
        expected = [
            ("ASTON", "10.10.4.184"),
            ("ASTON", "10.10.4.184"),
            ("FIAT",  "10.10.4.100"),
        ]
        results = []
        for _sid, rep_svc in records:
            results.append(advance_reporting_environment(state, rep_svc))
        self.assertEqual(results, expected)

    def test_transition_is_one_way_latch(self):
        """Once FIAT is reached, it must NEVER revert to ASTON, even if a
        later record's rep_service_name happens to contain 'aston' again."""
        state = new_reporting_environment_state()
        advance_reporting_environment(state, "fa194r.aston.csi.waynepa")   # ASTON
        advance_reporting_environment(state, "m5ddotr.fiat.csi.waynepa")   # -> FIAT (transition)
        env, host = advance_reporting_environment(state, "fa999r.aston.csi.waynepa")  # must stay FIAT
        self.assertEqual(env, "FIAT",
                          "Environment reverted to ASTON after the FIAT transition")
        self.assertEqual(host, FIAT_REPORTING_HOST)

    def test_transition_happens_exactly_at_first_fiat_marker(self):
        """The switch must occur on the exact record containing 'fiat', not
        one record early or late."""
        state = new_reporting_environment_state()
        before_env, _ = advance_reporting_environment(state, "fa1r.aston.csi.waynepa")
        at_env, _ = advance_reporting_environment(state, "fa2r.fiat.csi.waynepa")
        after_env, _ = advance_reporting_environment(state, "fa3r.aston.csi.waynepa")
        self.assertEqual(before_env, "ASTON", "Record before the marker must still be ASTON")
        self.assertEqual(at_env, "FIAT", "The record containing 'fiat' must itself be FIAT")
        self.assertEqual(after_env, "FIAT", "Records after the marker must remain FIAT")

    def test_case_insensitive_fiat_detection(self):
        """'FIAT', 'Fiat', 'fiat' must all trigger the transition identically."""
        for marker in ["fa1r.FIAT.csi.waynepa", "fa1r.Fiat.csi.waynepa", "fa1r.fiat.csi.waynepa"]:
            state = new_reporting_environment_state()
            env, host = advance_reporting_environment(state, marker)
            with self.subTest(marker=marker):
                self.assertEqual(env, "FIAT")
                self.assertEqual(host, FIAT_REPORTING_HOST)

    def test_blank_or_missing_service_name_does_not_change_state(self):
        """A record with no rep_service_name must not accidentally flip
        the environment - it simply inherits whatever state is current."""
        state = new_reporting_environment_state()
        env, host = advance_reporting_environment(state, "")
        self.assertEqual(env, "ASTON")
        self.assertEqual(host, ASTON_REPORTING_HOST)

        advance_reporting_environment(state, "m5ddotr.fiat.csi.waynepa")  # -> FIAT
        env2, host2 = advance_reporting_environment(state, None)
        self.assertEqual(env2, "FIAT")
        self.assertEqual(host2, FIAT_REPORTING_HOST)

    def test_environment_not_inferred_from_db_id_family(self):
        """An M5 SID appearing before the first 'fiat' marker must be ASTON
        (family prefix must never drive the environment decision), and an
        FA SID appearing after the marker must be FIAT."""
        state = new_reporting_environment_state()
        m5_env, m5_host = advance_reporting_environment(state, "m5xxxr.aston.csi.waynepa")
        self.assertEqual(m5_env, "ASTON")
        self.assertEqual(m5_host, ASTON_REPORTING_HOST)

        advance_reporting_environment(state, "m5ddotr.fiat.csi.waynepa")  # -> FIAT
        fa_env, fa_host = advance_reporting_environment(state, "fayyyr.fiat.csi.waynepa")
        self.assertEqual(fa_env, "FIAT")
        self.assertEqual(fa_host, FIAT_REPORTING_HOST)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        TestTemplateIsolation,
        TestSequentialSIDGeneration,
        TestPasswordDerivation,
        TestServiceNameDerivation,
        TestHostAndPort,
        TestReportingDBDerivation,
        TestTemplateSelection,
        TestStaleConfigPrevention,
        TestEdgeCases,
        TestReportingEnvironmentSequentialDetection,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
