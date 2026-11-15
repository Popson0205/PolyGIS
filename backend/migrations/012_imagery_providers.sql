-- Imagery providers an organization adds: tile services (XYZ, WMS) used as basemaps and layers, and STAC catalogues of
-- scenes to search and add. Keys stay on the server: tiles and scenes are fetched through PolyGIS.
CREATE TABLE IF NOT EXISTS imagery_providers (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid NOT NULL,
    name        text NOT NULL,
    kind        text NOT NULL CHECK (kind IN ('xyz', 'wms', 'stac')),
    url         text NOT NULL,                        -- a tile template, a WMS endpoint, or a STAC API root
    config      jsonb NOT NULL DEFAULT '{}'::jsonb,   -- attribution, max_zoom, layers (WMS), collections and asset (STAC), hosts
    auth        jsonb NOT NULL DEFAULT '{}'::jsonb,   -- {mode: none|query|header|basic, name, value, user, password}; never sent back
    enabled     boolean NOT NULL DEFAULT true,
    created_by  uuid,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS imagery_providers_org ON imagery_providers (org_id);
