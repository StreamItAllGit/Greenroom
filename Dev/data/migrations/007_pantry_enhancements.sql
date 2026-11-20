-- Migration 007: Pantry inventory and nutrition enhancements for household tracking
ALTER TABLE pantry_inventory ADD COLUMN category TEXT;
ALTER TABLE pantry_inventory ADD COLUMN stock_status TEXT;
ALTER TABLE pantry_inventory ADD COLUMN waste_tier TEXT;

ALTER TABLE nutrition_log ADD COLUMN target_calories REAL;
ALTER TABLE nutrition_log ADD COLUMN target_protein_g REAL;
ALTER TABLE nutrition_log ADD COLUMN target_carbs_g REAL;
ALTER TABLE nutrition_log ADD COLUMN target_fat_g REAL;
ALTER TABLE nutrition_log ADD COLUMN notes TEXT;
