"""ULS record layouts: record type code -> column names in pipe-delimited file order.

Derived from the FCC's official "Public Access Database Definitions"
(public_access_database_definitions_sql_v6_0_0) via the ISC-licensed
uls2sqlite project (https://git.sr.ht/~cg/uls2sqlite). FCC field
definitions are U.S. government work product.
"""

# fmt: off
TABLES: dict[str, tuple[str, ...]] = {
    "AC": ("record_type", "unique_system_identifier", "uls_file_number", "ebf_number", "call_sign", "aircraft_count", "type_of_carrier", "portable_indicator", "fleet_indicator", "n_number",),
    "AM": ("record_type", "unique_system_identifier", "uls_file_num", "ebf_number", "callsign", "operator_class", "group_code", "region_code", "trustee_callsign", "trustee_indicator", "physician_certification", "ve_signature", "systematic_callsign_change", "vanity_callsign_change", "vanity_relationship", "previous_callsign", "previous_operator_class", "trustee_name",),
    "AN": ("record_type", "unique_system_identifier", "uls_file_number", "ebf_number", "call_sign", "antenna_action_performed", "antenna_number", "location_number", "receive_zone_code", "antenna_type_code", "height_to_tip", "height_to_center_raat", "antenna_make", "antenna_model", "tilt", "polarization_code", "beamwidth", "gain", "azimuth", "height_above_avg_terrain", "diversity_height", "diversity_gain", "diversity_beam", "reflector_height", "reflector_width", "reflector_separation", "repeater_seq_num", "back_to_back_tx_dish_gain", "back_to_back_rx_dish_gain", "location_name", "passive_repeater_id", "alternative_cgsa_method", "path_number", "line_loss", "status_code", "status_date", "psd_nonpsd_methodology", "maximum_erp",),
    "AS": ("record_type", "unique_system_identifier", "uls_file_number", "ebf_number", "callsign", "assoc_callsign", "status_code", "status_date", "action_performed",),
    "AT": ("record_type", "unique_system_identifier", "uls_file_number", "ebf_number", "attachment_code", "attachment_description", "attachment_date", "attachment_file_name", "attachment_action_performed",),
    "CG": ("record_type", "unique_system_identifier", "uls_file_number", "ebf_number", "call_sign", "station_available", "public_correspondence", "station_identifier", "aeronautical_enroute_call_sign", "faa_office_notified", "date_faa_notified", "seeking_authorization", "regularly_engaged", "engaged", "public_mooring", "servicing", "fixed_station", "maritime_support", "aeronautical_fixed", "unicom", "search_and_rescue", "flight_test_uhf", "flight_test_manufacturer", "flight_test_parent_corporation", "flight_test_educational", "flight_school_certitication", "lighter_than_air", "ballooning", "located_at_airport", "radiodetermination_not_faa", "radiodetermination_equipment", "radiodetermination_public", "radiodetermination_elts", "civil_air_patrol", "aeronautical_enroute", "mobile_routine", "mobile_owner_operator", "mobile_agreement", "coast_ground_identifier", "selective_call_sign_identifier", "station_class", "status_code", "status_date",),
    "CO": ("record_type", "unique_system_identifier", "uls_file_num", "callsign", "comment_date", "description", "status_code", "status_date",),
    "CP": ("record_type", "unique_system_identifier", "uls_file_number", "ebf_number", "call_sign", "control_point_action_performed", "control_point_number", "control_address", "control_city", "state_code", "control_phone", "control_county", "status_code", "status_date",),
    "EM": ("record_type", "unique_system_identifier", "uls_file_number", "ebf_number", "call_sign", "location_number", "antenna_number", "frequency_assigned", "emission_action_performed", "emission_code", "digital_mod_rate", "digital_mod_type", "frequency_number", "status_code", "status_date", "emission_sequence_id",),
    "EN": ("record_type", "unique_system_identifier", "uls_file_number", "ebf_number", "call_sign", "entity_type", "licensee_id", "entity_name", "first_name", "mi", "last_name", "suffix", "phone", "fax", "email", "street_address", "city", "state", "zip_code", "po_box", "attention_line", "sgin", "frn", "applicant_type_code", "applicant_type_other", "status_code", "status_date", "lic_category_code", "linked_license_id", "linked_callsign",),
    "FR": ("record_type", "unique_system_identifier", "uls_file_number", "ebf_number", "call_sign", "frequency_action_performed", "location_number", "antenna_number", "class_station_code", "op_altitude_code", "frequency_assigned", "frequency_upper_band", "frequency_carrier", "time_begin_operations", "time_end_operations", "power_output", "power_erp", "tolerance", "frequency_ind", "status", "eirp", "transmitter_make", "transmitter_model", "auto_transmitter_power_control", "cnt_mobile_units", "cnt_mob_pagers", "freq_seq_id", "status_code", "status_date", "date_first_used",),
    "HD": ("record_type", "unique_system_identifier", "uls_file_number", "ebf_number", "call_sign", "license_status", "radio_service_code", "grant_date", "expired_date", "cancellation_date", "eligibility_rule_num", "applicant_type_code_reserved", "alien", "alien_government", "alien_corporation", "alien_officer", "alien_control", "revoked", "convicted", "adjudged", "involved_reserved", "common_carrier", "non_common_carrier", "private_comm", "fixed", "mobile", "radiolocation", "satellite", "developmental_or_sta", "interconnected_service", "certifier_first_name", "certifier_mi", "certifier_last_name", "certifier_suffix", "certifier_title", "gender", "african_american", "native_american", "hawaiian", "asian", "white", "ethnicity", "effective_date", "last_action_date", "auction_id", "reg_stat_broad_serv", "band_manager", "type_serv_broad_serv", "alien_ruling", "licensee_name_change", "whitespace_ind", "additional_cert_choice", "additional_cert_answer", "discontinuation_ind", "regulatory_compliance_ind", "eligibility_cert_900", "transition_plan_cert_900", "return_spectrum_cert_900", "payment_cert_900",),
    "HS": ("record_type", "unique_system_identifier", "uls_file_number", "callsign", "log_date", "code",),
    "LO": ("record_type", "unique_system_identifier", "uls_file_number", "ebf_number", "call_sign", "location_action_performed", "location_type_code", "location_class_code", "location_number", "site_status", "corresponding_fixed_location", "location_address", "location_city", "location_county", "location_state", "radius_of_operation", "area_of_operation_code", "clearance_indicator", "ground_elevation", "lat_degrees", "lat_minutes", "lat_seconds", "lat_direction", "long_degrees", "long_minutes", "long_seconds", "long_direction", "max_lat_degrees", "max_lat_minutes", "max_lat_seconds", "max_lat_direction", "max_long_degrees", "max_long_minutes", "max_long_seconds", "max_long_direction", "nepa", "quiet_zone_notification_date", "tower_registration_number", "height_of_support_structure", "overall_height_of_structure", "structure_type", "airport_id", "location_name", "units_hand_held", "units_mobile", "units_temp_fixed", "units_aircraft", "units_itinerant", "status_code", "status_date", "earth_agree",),
    "LM": ("record_type", "unique_system_identifier", "uls_file_number", "ebf_number", "callsign", "ext_implement_appr", "lm_eligibility_activity", "status_code", "status_date",),
    "MW": ("record_type", "unique_system_identifier", "uls_file_number", "ebf_number", "call_sign", "pack_indicator", "pack_registration_num", "pack_name", "type_of_operation", "smsa_code", "station_class", "cum_effect_is_major", "status_code", "status_date",),
    "OP": ("record_type", "unique_system_identifier", "uls_file_number", "ebf_number", "callsign", "location_number", "area_text_sequence_num", "area_of_operation", "status_code", "status_date",),
    "PA": ("record_type", "unique_system_identifier", "uls_file_number", "ebf_number", "callsign", "path_action_performed", "path_number", "transmit_location_number", "transmit_antenna_number", "receiver_location_number", "receiver_antenna_number", "mas_dems_subtype", "path_type_desc", "passive_receiver_indicator", "country_code", "interference_to_gso", "receiver_callsign", "angular_sep", "cert_no_alternative", "cert_no_interference", "status_code", "status_date", "link_start_date", "link_end_date",),
    "PC": ("record_type", "unique_system_identifier", "uls_file_number", "ebf_number", "call_sign", "action_performed", "location_number", "antenna_number", "frequency", "subscriber_call_sign", "city", "state", "lat_degrees", "lat_minutes", "lat_seconds", "lat_direction", "long_degrees", "long_minutes", "long_seconds", "long_direction", "point_of_com_frequency", "status_code", "status_date",),
    "SC": ("record_type", "unique_system_identifier", "uls_file_number", "ebf_number", "callsign", "special_condition_type", "special_condition_code", "status_code", "status_date",),
    "SF": ("record_type", "unique_system_identifier", "uls_file_number", "ebf_number", "callsign", "lic_freeform_cond_type", "unique_lic_freeform_id", "sequence_number", "lic_freeform_condition", "status_code", "status_date",),
    "SG": ("record_type", "unique_system_identifier", "uls_file_number", "ebf_number", "call_sign", "segment_action_performed", "path_number", "transmit_location", "transmit_antenna", "receiver_location", "receiver_antenna", "segment_number", "segment_length", "status_code", "status_date",),
    "SH": ("record_type", "unique_system_identifier", "uls_file_number", "ebf_number", "callsign", "type_of_authorization", "count_in_fleet", "general_class", "special_class", "ship_name", "ship_number", "international_voyages", "foreign_communications", "radiotelegraph", "mmsi_request", "gross_tonnage", "ship_length", "working_freq_s1", "working_freq_s2", "self_id_number", "comsat_id_number", "station_number", "required_cat_a", "required_cat_b", "required_cat_c", "required_cat_d", "required_cat_e",),
    "VC": ("record_type", "unique_system_identifier", "uls_file_number", "ebf_number", "request_sequence", "callsign_requested",),
}
# fmt: on

# ASR (Antenna Structure Registration, r_tower.zip) uses the same 2-char
# record codes with DIFFERENT layouts — never mix with TABLES above.
# Layouts derived from direct inspection of the 2026-09 weekly r_tower.zip
# (FCC does not publish per-position docs for these; positional names are
# used where semantics are unverified).
ASR_TABLES: dict[str, tuple[str, ...]] = {
    # RA: registration core. One row per registered structure.
    "RA": ("record_type", "record_kind", "registration_number",
           "unique_system_identifier", "file_number", "application_purpose",
           "f7", "f8", "status_code",
           "date_received", "date_granted", "date_constructed", "f13", "f14",
           "last_action_date", "f16", "f17",
           "contact_first_name", "contact_mi", "contact_last_name",
           "contact_suffix", "contact_title", "f23",
           "street_address", "city", "state", "county_fips", "zip_code",
           "height_structure_m", "ground_elevation_m", "height_overall_m",
           "height_agl_m", "structure_type",
           "faa_study_date", "faa_study_number", "faa_circular",
           "f37", "lighting_codes", "f39", "f40", "f41", "f42", "f43",
           "f44", "f45", "f46", "f47", "f48", "f49",),
    # CO: coordinates (DMS) per registration. coord_type 'T' = site point.
    "CO": ("record_type", "record_kind", "registration_number",
           "unique_system_identifier", "file_number", "coord_type",
           "lat_degrees", "lat_minutes", "lat_seconds", "lat_direction",
           "lat_packed_seconds",
           "long_degrees", "long_minutes", "long_seconds", "long_direction",
           "long_packed_seconds", "f17",),
    # EN: structure owner (entity_type 'O').
    "EN": ("record_type", "record_kind", "registration_number",
           "unique_system_identifier", "file_number", "entity_type",
           "f7", "f8", "f9", "entity_name", "f11", "f12", "f13", "f14",
           "phone", "fax", "email", "street_address", "f19", "f20",
           "city", "state", "zip_code", "attention_line", "f25",),
    # HS: history log.
    "HS": ("record_type", "record_kind", "registration_number",
           "unique_system_identifier", "file_number", "log_date",
           "description",),
    # RE: remarks (free text, often termination/construction notes).
    "RE": ("record_type", "record_kind", "registration_number",
           "unique_system_identifier", "file_number", "source", "log_date",
           "f8", "remark",),
    # SC: special conditions (FAA lighting/marking approvals).
    "SC": ("record_type", "record_kind", "registration_number",
           "unique_system_identifier", "file_number", "log_date",
           "condition_code", "condition",),
}


# Record types that carry a location with DMS coordinates (used for geo views).
GEO_TABLE = "LO"


# IBFS (International Bureau Filing System: satellite + earth station + section
# 214 records). Pipe-delimited like ULS but rows end with a "^|" terminator and
# dates are Sybase-style ("Sep 30 1986 12:00:00:000AM"). Layouts from the FCC's
# own CnvIbfs converter source (SUSS project) cross-checked with the 1998
# ibfs.txt DDL (Wayback), verified against the 2026-07 IBFS.zip dump.
IBFS_TABLES: dict[str, tuple[str, ...]] = {
    # main: the filing spine. filing_key joins everything; address_key ->
    # address (applicant/licensee), callsign -> space_sta.us_name for sats.
    "main": ("filing_key", "filing_state", "callsign", "file_number",
             "subsystem_code", "status_code", "status_date", "last_action",
             "last_action_date", "mts_number", "date_filed", "mellon_date",
             "date_grant", "date_deny", "date_dismiss", "date_surrender",
             "date_begin", "date_expire", "date_last_update",
             "aff_pub_notice_sw", "aff_pub_notice_date", "act_pub_notice_sw",
             "act_pub_notice_date", "submission_id", "fee_control_number",
             "app_type_code", "filing_other_text", "keyword1", "keyword2",
             "tower_cleared_sw", "date_blocked", "blocked_reason_code",
             "blocked_reason", "type_applicant_code", "applicant_other_text",
             "class_of_station_code", "class_other_text", "signer_name",
             "signer_title", "date_signed", "description", "address_key",
             "address_attention", "address_phone_num", "address_fax_num",
             "address_e_mail", "contact_key", "contact_attention",
             "contact_relationship", "contact_phone_num", "contact_fax_num",
             "contact_e_mail", "other_purpose_text", "streamlined_sw",
             "date_transferred", "confidential", "date_queued",
             "date_withdrew", "queue_flag", "filing_id", "date_created",
             "initiator_id", "public_pn_note_key", "fee_exempt_sw",
             "fee_exempt_reason", "remittance_id", "next_step", "released_by",
             "date_released", "date_adopted", "order_da_number",),
    # station: callsign-bearing records (incl. foreign broadcast stations)
    "station": ("station_key", "filing_key", "station_callsign",
                "station_broadcast_type", "station_frequency",
                "station_channel", "station_city", "country_code",),
    # site: transmitter locations with DMS coords; site_key -> anten
    "site": ("site_key", "filing_key", "site_id", "site_description",
             "contact_person", "site_street1", "site_street2", "site_city",
             "site_county", "site_state", "site_zipcode", "site_telephone",
             "site_elevation", "lat_degrees", "lat_minutes", "lat_seconds",
             "lat_direction", "long_degrees", "long_minutes", "long_seconds",
             "long_direction", "nad_ind", "num_vsats_built",
             "vsat_report_date", "faa_coord_sw", "comply_25209a_sw",
             "comply_25209a2_sw", "remote_control_sw",
             "foreign_freq_coord_req_sw", "freq_coord_req_sw",
             "area_of_operation_code", "faa_coord_not_req_sw",
             "comply_25211_sw",),
    # earth_sta: earth-station STA (temporary authority) detail per filing
    "earth_sta": ("filing_key", "type_sta_code", "requested_date", "city",
                  "state_code", "lat_degrees", "lat_minutes", "lat_seconds",
                  "lat_direction", "long_degrees", "long_minutes",
                  "long_seconds", "long_direction",),
    # space_sta: the satellite registry (us_name = US callsign e.g. KS30)
    "space_sta": ("space_station_key", "us_name", "itu_name", "orbit_location",
                  "verbose", "inactive_date", "long_hemi",),
    # anten: antenna detail; tower_id can be an ASR registration number
    "anten": ("antenna_key", "antenna_id", "site_key", "diameter",
              "diameter_minor", "diameter_major", "height_bldg_agl",
              "height_max_agl", "height_max_amsl", "height_max_aroof",
              "manufacturer", "max_input_power", "max_output_eirp", "model",
              "quantity", "tower_id", "tower_cleared_sw",),
    # freq: emission + zero-packed MHz range per antenna (anten.antenna_key)
    "freq": ("frequency_key", "antenna_key", "polarization_code", "eirp",
             "eirp_density", "emission", "frequency_lower", "frequency_upper",
             "trans_mode", "modulation", "f11", "f12", "f13",),
    # address: applicant/licensee entities; frn links back to ULS
    "address": ("address_key", "address_id", "address_name", "dba_name",
                "street1", "street2", "city", "state_code", "zipcode",
                "country_code", "soundex", "dba_soundex", "frn", "f14",),
    # stat_track: status history per filing
    "stat_track": ("filing_key", "purpose", "status_date", "status_code",),
    # filenum_xref: legacy file number -> filing key
    "filenum_xref": ("legacy_file_number", "filing_key",),
}


def get_columns(record_type: str) -> tuple[str, ...] | None:
    """Column names for a record type code (e.g. 'HD'), or None if unknown."""
    return TABLES.get(record_type.upper())
