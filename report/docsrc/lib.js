const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType, ImageRun, Table, TableRow, TableCell,
  WidthType, ShadingType, BorderStyle, LevelFormat, Footer, PageNumber, ExternalHyperlink,
} = require("docx");

const FIG = process.argv[2];
const CONTENT_W = 9026; // A4, 여백 1인치
const FONT = "Calibri";
const MONO = "Consolas";
const BORDER = { style: BorderStyle.SINGLE, size: 4, color: "BFBFBF" };
const BORDERS = { top: BORDER, bottom: BORDER, left: BORDER, right: BORDER };

function runs(text, base = {}) {
  // **굵게**, `코드` 만 지원
  const out = [];
  for (const part of text.split(/(\*\*[^*]+\*\*|`[^`]+`)/)) {
    if (!part) continue;
    if (part.startsWith("**")) out.push(new TextRun({ ...base, text: part.slice(2, -2), bold: true }));
    else if (part.startsWith("`")) out.push(new TextRun({ ...base, text: part.slice(1, -1), font: MONO, size: 20 }));
    else out.push(new TextRun({ ...base, text: part }));
  }
  return out;
}

const p = (text, opts = {}) => new Paragraph({ spacing: { after: 140, line: 288 }, ...opts, children: runs(text) });
const h1 = (text, pageBreakBefore = false) => new Paragraph({ heading: HeadingLevel.HEADING_1, pageBreakBefore, children: [new TextRun(text)] });
const h2 = (text, pageBreakBefore = false) => new Paragraph({ heading: HeadingLevel.HEADING_2, pageBreakBefore, children: [new TextRun(text)] });
const bullet = text => new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 70, line: 276 }, children: runs(text) });
const step = text => new Paragraph({ numbering: { reference: "steps", level: 0 }, spacing: { after: 90, line: 276 }, children: runs(text) });

function code(lines) {
  return lines.map((line, i) => new Paragraph({
    shading: { type: ShadingType.CLEAR, fill: "F3F3F3" },
    spacing: { after: i === lines.length - 1 ? 160 : 0, line: 260 },
    indent: { left: 140 },
    children: [new TextRun({ text: line || " ", font: MONO, size: 19 })],
  }));
}

function pngSize(file) {
  const buf = fs.readFileSync(file);
  return [buf.readUInt32BE(16), buf.readUInt32BE(20)];
}

function imageRun(name, width, size) {
  const file = path.join(FIG, name);
  const [w, h] = size || pngSize(file);
  return new ImageRun({
    type: name.endsWith(".jpg") ? "jpg" : "png",
    data: fs.readFileSync(file),
    transformation: { width, height: Math.round(width * h / w) },
  });
}

function figure(name, caption, width = 600, size) {
  return [
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 120, after: 60 }, keepNext: true, children: [imageRun(name, width, size)] }),
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 220 }, children: [new TextRun({ text: caption, italics: true, size: 19, color: "404040" })] }),
  ];
}

function cell(text, width, opts = {}) {
  return new TableCell({
    borders: BORDERS,
    width: { size: width, type: WidthType.DXA },
    margins: { top: 60, bottom: 60, left: 100, right: 100 },
    shading: opts.header ? { type: ShadingType.CLEAR, fill: "E7EEF7" } : undefined,
    children: [new Paragraph({
      alignment: opts.align || AlignmentType.LEFT,
      children: runs(String(text), { size: 19, bold: !!opts.header }),
    })],
  });
}

function table(headers, rows, widths, numeric = []) {
  const total = widths.reduce((a, b) => a + b, 0);
  const scaled = widths.map(w => Math.round(w * CONTENT_W / total));
  scaled[scaled.length - 1] += CONTENT_W - scaled.reduce((a, b) => a + b, 0);
  const align = i => (numeric.includes(i) ? AlignmentType.RIGHT : AlignmentType.LEFT);
  return [
    new Table({
      width: { size: CONTENT_W, type: WidthType.DXA },
      columnWidths: scaled,
      rows: [
        new TableRow({ tableHeader: true, children: headers.map((h, i) => cell(h, scaled[i], { header: true, align: align(i) })) }),
        ...rows.map(row => new TableRow({ cantSplit: true, children: row.map((v, i) => cell(v, scaled[i], { align: align(i) })) })),
      ],
    }),
    new Paragraph({ spacing: { after: 200 }, children: [] }),
  ];
}

const caption = text => new Paragraph({ spacing: { before: 80, after: 80 }, keepNext: true, children: [new TextRun({ text, italics: true, size: 19, color: "404040" })] });

function imageRow(items, width) {
  // 휴대폰 화면 여러 장을 가로로
  const colW = Math.floor(CONTENT_W / items.length);
  const none = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
  const noBorders = { top: none, bottom: none, left: none, right: none };
  return [
    new Table({
      width: { size: colW * items.length, type: WidthType.DXA },
      columnWidths: items.map(() => colW),
      rows: [
        new TableRow({ children: items.map(([name]) => new TableCell({ borders: noBorders, width: { size: colW, type: WidthType.DXA }, children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [imageRun(name, width)] })] })) }),
        new TableRow({ children: items.map(([, text]) => new TableCell({ borders: noBorders, width: { size: colW, type: WidthType.DXA }, children: [new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 60 }, children: [new TextRun({ text, italics: true, size: 19, color: "404040" })] })] })) }),
      ],
    }),
    new Paragraph({ spacing: { after: 220 }, children: [] }),
  ];
}

function titleBlock(title, subtitle, lines) {
  return [
    new Paragraph({ spacing: { before: 0, after: 80 }, children: [new TextRun({ text: title, bold: true, size: 44 })] }),
    new Paragraph({ spacing: { after: 160 }, children: [new TextRun({ text: subtitle, size: 26, color: "404040" })] }),
    ...lines.map(line => new Paragraph({ spacing: { after: 30 }, children: runs(line, { size: 21, color: "404040" }) })),
    new Paragraph({ spacing: { after: 240 }, border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "BFBFBF", space: 8 } }, children: [] }),
  ];
}

const link = (text, url) => new Paragraph({ spacing: { after: 140 }, children: [new ExternalHyperlink({ link: url, children: [new TextRun({ text, style: "Hyperlink" })] })] });

async function build(children, outFile, title) {
  const heading = (size, before, after) => ({ run: { font: FONT, size, bold: true, color: "1F3552" }, paragraph: { spacing: { before, after }, keepNext: true } });
  const doc = new Document({
    title,
    styles: {
      default: {
        document: { run: { font: FONT, size: 22 } },
        heading1: heading(30, 320, 140),
        heading2: heading(24, 240, 100),
      },
    },
    numbering: {
      config: [
        { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 500, hanging: 260 } } } }] },
        { reference: "steps", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 500, hanging: 320 } } } }] },
      ],
    },
    sections: [{
      properties: { page: { margin: { top: 1440, bottom: 1440, left: 1440, right: 1440 } } },
      footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ children: [PageNumber.CURRENT], size: 18, color: "808080" })] })] }) },
      children: children.flat(Infinity),
    }],
  });
  fs.writeFileSync(outFile, await Packer.toBuffer(doc));
  console.log("wrote", outFile);
}

module.exports = { p, h1, h2, bullet, step, code, figure, table, caption, imageRow, titleBlock, link, build };
