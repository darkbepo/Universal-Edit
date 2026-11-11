/**
 * The built-in Word template of the DOCX writer: the parts python-docx's default template
 * (`docx/templates/default.docx`) contributes to a document the desktop app writes, cut down
 * to what a valid package needs. Style values are python-docx's; theme fonts and colours are
 * written out (Calibri headings, Cambria body text) because the package carries no theme.
 */

export const XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n';

export const NS = {
  w: "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
  r: "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
  wp: "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
  a: "http://schemas.openxmlformats.org/drawingml/2006/main",
  pic: "http://schemas.openxmlformats.org/drawingml/2006/picture",
  rels: "http://schemas.openxmlformats.org/package/2006/relationships",
  ct: "http://schemas.openxmlformats.org/package/2006/content-types",
} as const;

const REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
export const RT = {
  officeDocument: `${REL}/officeDocument`,
  styles: `${REL}/styles`,
  numbering: `${REL}/numbering`,
  settings: `${REL}/settings`,
  image: `${REL}/image`,
  hyperlink: `${REL}/hyperlink`,
  extended: `${REL}/extended-properties`,
  core: "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
} as const;

const OXML = "application/vnd.openxmlformats-officedocument";
export const CT = {
  rels: "application/vnd.openxmlformats-package.relationships+xml",
  xml: "application/xml",
  document: `${OXML}.wordprocessingml.document.main+xml`,
  styles: `${OXML}.wordprocessingml.styles+xml`,
  numbering: `${OXML}.wordprocessingml.numbering+xml`,
  settings: `${OXML}.wordprocessingml.settings+xml`,
  core: "application/vnd.openxmlformats-package.core-properties+xml",
  app: `${OXML}.extended-properties+xml`,
} as const;

const W_ROOT = `xmlns:w="${NS.w}" xmlns:r="${NS.r}"`;

/** Hyperlink and Code: added by the writer when a template lacks them (as on the desktop). */
export const HYPERLINK_STYLE =
  '<w:style w:type="character" w:customStyle="1" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/>' +
  '<w:rPr><w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr></w:style>';
export const codeStyle = (basedOn: string): string =>
  `<w:style w:type="paragraph" w:customStyle="1" w:styleId="Code"><w:name w:val="Code"/>` +
  `<w:basedOn w:val="${basedOn}"/><w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/>` +
  '<w:sz w:val="20"/></w:rPr></w:style>';

const HEADING_FONT =
  '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" w:cs="Times New Roman"/>';
const heading = (n: number, before: number, color: string, size: string) =>
  `<w:style w:type="paragraph" w:styleId="Heading${n}"><w:name w:val="heading ${n}"/>` +
  '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:uiPriority w:val="9"/>' +
  `${n > 1 ? "<w:unhideWhenUsed/>" : ""}<w:qFormat/><w:pPr><w:keepNext/><w:keepLines/>` +
  `<w:spacing w:before="${before}" w:after="0"/><w:outlineLvl w:val="${n - 1}"/></w:pPr>` +
  `<w:rPr>${HEADING_FONT}<w:b/><w:bCs/><w:color w:val="${color}"/>${size}</w:rPr></w:style>`;
const BORDER = 'w:val="single" w:sz="4" w:space="0" w:color="auto"';
const CELL_MARGINS =
  '<w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="108" w:type="dxa"/>' +
  '<w:bottom w:w="0" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar>';

/** The styles a template lacking them gets copied from here (by name, like the desktop). */
export const STYLES =
  `${XML_HEAD}<w:styles ${W_ROOT}><w:docDefaults><w:rPrDefault><w:rPr>` +
  '<w:rFonts w:ascii="Cambria" w:hAnsi="Cambria" w:eastAsia="Cambria" w:cs="Times New Roman"/>' +
  '<w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="en-US" w:eastAsia="en-US" w:bidi="ar-SA"/>' +
  '</w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="200" w:line="276" w:lineRule="auto"/>' +
  "</w:pPr></w:pPrDefault></w:docDefaults>" +
  '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>' +
  '<w:style w:type="character" w:default="1" w:styleId="DefaultParagraphFont"><w:name w:val="Default Paragraph Font"/>' +
  '<w:uiPriority w:val="1"/><w:semiHidden/><w:unhideWhenUsed/></w:style>' +
  '<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/>' +
  '<w:uiPriority w:val="99"/><w:semiHidden/><w:unhideWhenUsed/><w:tblPr><w:tblInd w:w="0" w:type="dxa"/>' +
  `${CELL_MARGINS}</w:tblPr></w:style>` +
  '<w:style w:type="numbering" w:default="1" w:styleId="NoList"><w:name w:val="No List"/>' +
  '<w:uiPriority w:val="99"/><w:semiHidden/><w:unhideWhenUsed/></w:style>' +
  heading(1, 480, "365F91", '<w:sz w:val="28"/><w:szCs w:val="28"/>') +
  heading(2, 200, "4F81BD", '<w:sz w:val="26"/><w:szCs w:val="26"/>') +
  heading(3, 200, "4F81BD", "") +
  '<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/>' +
  '<w:next w:val="Normal"/><w:uiPriority w:val="29"/><w:qFormat/><w:rPr><w:i/><w:iCs/>' +
  '<w:color w:val="000000"/></w:rPr></w:style>' +
  '<w:style w:type="paragraph" w:styleId="ListBullet"><w:name w:val="List Bullet"/><w:basedOn w:val="Normal"/>' +
  '<w:uiPriority w:val="99"/><w:unhideWhenUsed/><w:pPr><w:contextualSpacing/></w:pPr></w:style>' +
  '<w:style w:type="paragraph" w:styleId="ListNumber"><w:name w:val="List Number"/><w:basedOn w:val="Normal"/>' +
  '<w:uiPriority w:val="99"/><w:unhideWhenUsed/><w:pPr><w:contextualSpacing/></w:pPr></w:style>' +
  '<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:basedOn w:val="TableNormal"/>' +
  '<w:uiPriority w:val="59"/><w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr>' +
  `<w:tblPr><w:tblInd w:w="0" w:type="dxa"/><w:tblBorders><w:top ${BORDER}/><w:left ${BORDER}/>` +
  `<w:bottom ${BORDER}/><w:right ${BORDER}/><w:insideH ${BORDER}/><w:insideV ${BORDER}/></w:tblBorders>` +
  `${CELL_MARGINS}</w:tblPr></w:style>` +
  HYPERLINK_STYLE +
  codeStyle("Normal") +
  "</w:styles>";

/** python-docx's page: US Letter, 1.25" side margins (8640 twips of text width). */
export const DOCUMENT =
  `${XML_HEAD}<w:document ${W_ROOT} xmlns:wp="${NS.wp}" xmlns:a="${NS.a}" xmlns:pic="${NS.pic}">` +
  '<w:body><w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1800" ' +
  'w:bottom="1440" w:left="1800" w:header="720" w:footer="720" w:gutter="0"/><w:cols w:space="720"/>' +
  '<w:docGrid w:linePitch="360"/></w:sectPr></w:body></w:document>';

export const NUMBERING = `${XML_HEAD}<w:numbering ${W_ROOT}></w:numbering>`;

export const SETTINGS =
  `${XML_HEAD}<w:settings ${W_ROOT}><w:defaultTabStop w:val="720"/>` +
  '<w:characterSpacingControl w:val="doNotCompress"/><w:compat><w:compatSetting ' +
  'w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" w:val="14"/>' +
  "</w:compat></w:settings>";

/** Fixed dates, so the same content always gives the same bytes. */
export const CORE =
  `${XML_HEAD}<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" ` +
  'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" ' +
  'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title/><dc:creator/>' +
  "<cp:lastModifiedBy/><cp:revision>1</cp:revision>" +
  '<dcterms:created xsi:type="dcterms:W3CDTF">2000-01-01T00:00:00Z</dcterms:created>' +
  '<dcterms:modified xsi:type="dcterms:W3CDTF">2000-01-01T00:00:00Z</dcterms:modified>' +
  "</cp:coreProperties>";

export const APP =
  `${XML_HEAD}<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">` +
  "<Application>Universal Media Converter</Application></Properties>";

const rel = (id: string, type: string, target: string) =>
  `<Relationship Id="${id}" Type="${type}" Target="${target}"/>`;

export const ROOT_RELS =
  `${XML_HEAD}<Relationships xmlns="${NS.rels}">` +
  rel("rId1", RT.officeDocument, "word/document.xml") +
  rel("rId2", RT.core, "docProps/core.xml") +
  rel("rId3", RT.extended, "docProps/app.xml") +
  "</Relationships>";

export const DOCUMENT_RELS =
  `${XML_HEAD}<Relationships xmlns="${NS.rels}">` +
  rel("rId1", RT.styles, "styles.xml") +
  rel("rId2", RT.numbering, "numbering.xml") +
  rel("rId3", RT.settings, "settings.xml") +
  "</Relationships>";

const override = (name: string, type: string) =>
  `<Override PartName="${name}" ContentType="${type}"/>`;

export const CONTENT_TYPES =
  `${XML_HEAD}<Types xmlns="${NS.ct}">` +
  `<Default Extension="rels" ContentType="${CT.rels}"/><Default Extension="xml" ContentType="${CT.xml}"/>` +
  override("/word/document.xml", CT.document) +
  override("/word/styles.xml", CT.styles) +
  override("/word/numbering.xml", CT.numbering) +
  override("/word/settings.xml", CT.settings) +
  override("/docProps/core.xml", CT.core) +
  override("/docProps/app.xml", CT.app) +
  "</Types>";

/** The built-in template's parts, in zip order. */
export const PARTS: [string, string][] = [
  ["[Content_Types].xml", CONTENT_TYPES],
  ["_rels/.rels", ROOT_RELS],
  ["docProps/core.xml", CORE],
  ["docProps/app.xml", APP],
  ["word/document.xml", DOCUMENT],
  ["word/_rels/document.xml.rels", DOCUMENT_RELS],
  ["word/styles.xml", STYLES],
  ["word/numbering.xml", NUMBERING],
  ["word/settings.xml", SETTINGS],
];
