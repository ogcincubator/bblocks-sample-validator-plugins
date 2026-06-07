"""
Sample bblocks validator plugins.

ZipValidator   — checks that a ZIP file is not corrupt (stdlib only).
WktValidator   — checks that a WKT geometry is syntactically and topologically valid (shapely).
XsdValidator   — validates XML/GML files against one or more XSD schemas declared as bblock
                 validation resources (lxml).

All follow the bblocks validator plugin contract:
  - class attributes `mime_types` and/or `file_extensions` declare which files are handled
  - validate(self, meta) receives a meta namespace and returns list[dict] | None
      meta.input_path              absolute path to the file
      meta.mime_type               MIME type string or None
      meta.display_filename        original filename for use in messages
      meta.schema_ref              schema ref from snippet, or None
      meta.context                 namespace with:
        .bblock_id                   building block identifier
        .bblock_name                 building block name
        .register_base_url           base URL of the register
        .validation_resources        list of {ref, format[, conformsTo]} dicts where ref
                                     is a cwd-relative local path or a URL
  - each dict: {"message": str, "is_error": bool, "payload"?: dict}
  - return None or [] to signal "nothing to report"
"""
import zipfile

# Both the https and http forms of the W3C XML Schema namespace are in use.
_XSD_CONFORMSTO = {
    'https://www.w3.org/2001/XMLSchema',
    'http://www.w3.org/2001/XMLSchema',
}


class ZipValidator:
    mime_types = ['application/zip', 'application/x-zip-compressed']
    file_extensions = ['.zip']

    def validate(self, meta):
        try:
            with zipfile.ZipFile(meta.input_path) as zf:
                bad_file = zf.testzip()
        except zipfile.BadZipFile as exc:
            return [{'message': f'Invalid ZIP file: {exc}', 'is_error': True}]
        except Exception as exc:
            return [{'message': f'Could not open ZIP file: {exc}', 'is_error': True}]

        if bad_file is not None:
            return [{'message': f'ZIP archive is corrupt (first bad entry: {bad_file})',
                     'is_error': True}]

        return [{'message': f'ZIP archive is valid ({meta.display_filename})',
                 'is_error': False}]


class WktValidator:
    mime_types = ['text/wkt']
    file_extensions = ['.wkt']

    def validate(self, meta):
        try:
            with open(meta.input_path, encoding='utf-8') as f:
                content = f.read().strip()
        except Exception as exc:
            return [{'message': f'Could not read file: {exc}', 'is_error': True}]

        if not content:
            return [{'message': 'File is empty — no WKT geometry to validate',
                     'is_error': True}]

        from shapely import wkt as shapely_wkt
        from shapely.errors import ShapelyError

        try:
            geom = shapely_wkt.loads(content)
        except ShapelyError as exc:
            return [{'message': f'Invalid WKT syntax: {exc}', 'is_error': True}]
        except Exception as exc:
            return [{'message': f'WKT parse error: {exc}', 'is_error': True}]

        entries = []

        if geom.is_empty:
            entries.append({'message': 'WKT geometry is empty', 'is_error': False,
                            'payload': {'subsection': 'Warnings'}})

        if not geom.is_valid:
            from shapely.validation import explain_validity
            reason = explain_validity(geom)
            entries.append({'message': f'Geometry is topologically invalid: {reason}',
                            'is_error': True})
        else:
            entries.append({'message': f'WKT geometry is valid (type: {geom.geom_type})',
                            'is_error': False})

        return entries


class XsdValidator:
    """Validates XML/GML files against XSD schemas declared as bblock validation resources.

    The validator is a no-op (returns None) when the bblock declares no validation resources
    with conformsTo matching the W3C XML Schema namespace URI.

    Example bblock.json declaration:
        "resources": [{
            "role": "validation",
            "ref": "assets/schema.xsd",
            "format": "application/xml",
            "conformsTo": "https://www.w3.org/2001/XMLSchema"
        }]
    """

    mime_types = ['application/xml', 'text/xml', 'application/gml+xml']
    file_extensions = ['.xml', '.gml']

    def validate(self, meta):
        specs = [
            r for r in (getattr(meta.context, 'validation_resources', None) or [])
            if r.get('conformsTo') in _XSD_CONFORMSTO
        ]
        if not specs:
            return None

        from lxml import etree

        try:
            doc = etree.parse(meta.input_path)
        except etree.XMLSyntaxError as exc:
            return [{'message': f'XML syntax error: {exc}', 'is_error': True}]
        except Exception as exc:
            return [{'message': f'Could not parse XML: {exc}', 'is_error': True}]

        entries = []
        for spec in specs:
            ref = spec['ref']
            schema_label = ref.rsplit('/', 1)[-1]

            try:
                xsd = etree.XMLSchema(etree.parse(ref))
            except Exception as exc:
                entries.append({
                    'message': f'Could not load XSD {schema_label!r}: {exc}',
                    'is_error': True,
                    'payload': {'subsection': schema_label, 'schema': ref},
                })
                continue

            if xsd.validate(doc):
                entries.append({
                    'message': f'Document is valid against {schema_label!r}',
                    'is_error': False,
                    'payload': {'subsection': schema_label},
                })
            else:
                for error in xsd.error_log:
                    entries.append({
                        'message': f'{error.message} (line {error.line})',
                        'is_error': True,
                        'payload': {
                            'subsection': schema_label,
                            'line': error.line,
                            'column': error.column,
                        },
                    })

        return entries