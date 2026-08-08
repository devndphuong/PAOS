-- Migration 002 — bộ đếm event_seq trong counters, cùng cơ chế next_pid() (lát cắt 2, doc 19 P-M0-2).
INSERT INTO counters(name, value) VALUES ('event_seq', 0);
