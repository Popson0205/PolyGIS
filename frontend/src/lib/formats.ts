/** The spatial file formats Add data takes (every common one GDAL reads), for the file picker and the words beside it. */
export const VECTOR_EXT = [".geojson", ".json", ".geojsonl", ".geojsons", ".ndjson", ".topojson", ".zip", ".shp", ".shx", ".dbf", ".prj", ".cpg",
  ".gpkg", ".kml", ".kmz", ".csv", ".txt", ".tsv", ".dxf", ".dgn", ".gpx", ".gml", ".xml", ".fgb", ".tab", ".mif", ".mid", ".map", ".id", ".dat",
  ".sqlite", ".spatialite", ".db", ".osm", ".pbf", ".e00", ".gmt", ".vrt", ".jml", ".georss", ".s57", ".000", ".pmtiles"];
export const RASTER_EXT = [".tif", ".tiff", ".geotiff", ".cog", ".img", ".asc", ".jp2", ".j2k", ".hgt", ".dem", ".bil", ".bip", ".bsq", ".hdr", ".nc",
  ".ecw", ".sid", ".ter", ".xyz", ".grd", ".dt0", ".dt1", ".dt2", ".ers", ".rst", ".rdc", ".sdat", ".sgrd", ".grb", ".grb2", ".grib", ".grib2",
  ".ntf", ".nitf", ".bt", ".hf2", ".rsw", ".mrf", ".bag", ".kea", ".hdf", ".h5", ".he5", ".pix", ".lcp", ".flt", ".adf", ".mbtiles",
  ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".pgm", ".ppm",
  // world files and other companions chosen with the data
  ".tfw", ".tifw", ".jgw", ".jpgw", ".jpw", ".pgw", ".pngw", ".gfw", ".bpw", ".wld", ".j2w", ".aux", ".aux.xml", ".ovr", ".clr", ".stx"];
export const ACCEPT = Array.from(new Set([...VECTOR_EXT, ...RASTER_EXT])).join(",");
export const PICTURE = /\.(jpe?g|png|gif|bmp|webp|tiff?)$/i;
