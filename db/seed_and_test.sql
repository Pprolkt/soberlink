-- Phase 1 test data: one venue, one staff member, one active session with drinks
-- Run this AFTER schema.sql to sanity-check relationships work

INSERT INTO venues (venue_name, venue_code) VALUES ('Test Bar', 'VEN-001');

INSERT INTO venue_staff (venue_id, staff_login, role)
SELECT venue_id, 'staff01', 'rsa_staff' FROM venues WHERE venue_code = 'VEN-001';

INSERT INTO sessions (token_hash, expires_at)
VALUES ('TEST_HASH_0000000000000000000000000000000000000000000000000000', now() + interval '8 hours');

-- Record two drinks 20 minutes apart (this should trigger the Option B velocity flag later in Phase 8)
INSERT INTO drink_events (session_id, venue_id, drink_type, volume_ml, abv, standard_drinks, recorded_at)
SELECT s.session_id, v.venue_id, 'beer_375ml', 375, 4.80, 1.4, now() - interval '25 minutes'
FROM sessions s, venues v WHERE v.venue_code = 'VEN-001';

INSERT INTO drink_events (session_id, venue_id, drink_type, volume_ml, abv, standard_drinks, recorded_at)
SELECT s.session_id, v.venue_id, 'spirit_30ml', 30, 40.00, 1.0, now() - interval '5 minutes'
FROM sessions s, venues v WHERE v.venue_code = 'VEN-001';

-- Log the matching venue events
INSERT INTO venue_events (session_id, venue_id, event_type)
SELECT s.session_id, v.venue_id, 'CHECK_IN' FROM sessions s, venues v WHERE v.venue_code = 'VEN-001';

INSERT INTO venue_events (session_id, venue_id, event_type)
SELECT s.session_id, v.venue_id, 'DRINK_RECORDED' FROM sessions s, venues v WHERE v.venue_code = 'VEN-001';

-- Sanity check query: total standard drinks in the last 3 hours for this session
SELECT s.session_id,
       SUM(d.standard_drinks) AS drinks_3h,
       COUNT(*) AS drink_count
FROM sessions s
JOIN drink_events d ON d.session_id = s.session_id
WHERE d.recorded_at >= now() - interval '3 hours'
GROUP BY s.session_id;