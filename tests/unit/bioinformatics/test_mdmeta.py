"""Unit tests for Markdown script-metadata parsing (mdmeta)."""

from __future__ import annotations

import json

from octop.infra.bioinformatics.mdmeta import parse_script_md


def test_full_zh_doc() -> None:
    md = """# fastp 质控

## 名称: fastp 质控
## 版本: 2.1.0
## 分类: qc
## 描述: 去接头并做质量控制
## 有效期开始: 2026-01-01
## 有效期结束: 2027-06-30

## 输入
- reads: fastq, fq
- samplesheet: csv, tsv, multiple

## 输出
- report: html, json
"""
    meta = parse_script_md(md)
    assert meta.name == "fastp 质控"
    assert meta.version == "2.1.0"
    assert meta.category == "qc"
    assert "去接头" in meta.description
    assert meta.valid_from == 1767225600  # 2026-01-01 UTC
    assert meta.valid_until == 1814313600  # 2027-06-30 UTC

    inputs = json.loads(meta.inputs)
    assert inputs == [
        {"label": "reads", "extensions": ["fastq", "fq"], "multiple": False},
        {"label": "samplesheet", "extensions": ["csv", "tsv"], "multiple": True},
    ]
    outputs = json.loads(meta.outputs)
    assert outputs == [{"label": "report", "extensions": ["html", "json"], "multiple": False}]


def test_en_doc_with_title_fallback() -> None:
    md = """# BWA alignment

## Version: 0.7.17
## Description: Align reads to a reference genome

## Inputs
- reference: fasta, fa
- reads: fastq
"""
    meta = parse_script_md(md)
    assert meta.name == "BWA alignment"
    assert meta.version == "0.7.17"
    assert meta.description == "Align reads to a reference genome"
    inputs = json.loads(meta.inputs)
    assert [s["label"] for s in inputs] == ["reference", "reads"]


def test_minimal_doc_defaults() -> None:
    meta = parse_script_md("# just a title\n\nsome prose\n")
    assert meta.name == "just a title"
    assert meta.version == ""
    assert meta.inputs == "[]"
    assert meta.outputs == "[]"
    assert meta.valid_from is None


def test_unix_ts_and_slash_dates() -> None:
    meta = parse_script_md("## 有效期开始: 1780000000\n## 有效期结束: 2026/3/15\n")
    assert meta.valid_from == 1780000000
    assert meta.valid_until == 1773532800  # 2026-03-15 UTC


def test_empty_and_garbage() -> None:
    assert parse_script_md("").name == ""
    meta = parse_script_md("no headings at all\n- not: a section\n")
    assert meta.inputs == "[]"


def test_lenient_prose_description() -> None:
    """Docs without a 描述 heading: first prose paragraph becomes the description."""
    meta = parse_script_md("# bwa\n\n将 reads 比对到参考基因组。\n\n更多细节……\n")
    assert meta.name == "bwa"
    assert meta.description == "将 reads 比对到参考基因组。"


def test_lenient_bare_list_items() -> None:
    """'- text' under 输入/输出 without a colon → slot accepting any extension."""
    meta = parse_script_md("## 输入\n- 双端 reads 文件\n- 参考基因组\n")
    inputs = json.loads(meta.inputs)
    assert inputs == [
        {"label": "双端 reads 文件", "extensions": [], "multiple": False},
        {"label": "参考基因组", "extensions": [], "multiple": False},
    ]
    # prose fallback must not swallow list items outside sections
    meta2 = parse_script_md("- 游离列表项\n正文段落\n")
    assert meta2.description == "正文段落"


DADA2_DOC = """# DADA2 - ASV 推断与降噪模块

## 基本信息

| 字段 | 值 |
|------|-----|
| **Tool Name** | dada2 |
| **Description** | 基于错误模型的扩增子序列变异（ASV）推断工具 |
| **适用范围** | 16S/18S/ITS 扩增子测序 |
| **估算机时** | 8.0 小时 |
| **报价分数** | 14.4 |

---

## 输入

| 文件名 | 描述 | 格式 | 适用组学 | 适用物种 | 示例文件 |
|--------|------|------|----------|----------|----------|
| manifest.tsv | 样本与序列文件路径的对应关系表 | TSV | 16S/18S/ITS | 通用 | (manifest.tsv) |
| ${sample}.fastq | 质控后的序列文件 | FASTQ | 16S/18S/ITS | 通用 | (D1.fastq.gz) |

---

## 输出

| 文件名 | 描述 | 格式 | 适用组学 | 适用物种 | 示例文件 |
|--------|------|------|----------|----------|----------|
| featureSeqs.qza | ASV 代表序列（QIIME2） | QZA | 16S/18S/ITS | 通用 | - |
| feature-table.tsv | ASV 丰度表（已排序） | TSV | 16S/18S/ITS | 通用 | - |

---

## 执行命令

### 参数说明

| 参数 | 说明 | 必需 | 默认值 | 示例 |
|------|------|------|--------|------|
| -m, --manifest | manifest 文件路径 | 是 | - | manifest.tsv |

## 下游工具

| 工具 | 输入文件 | 说明 |
|------|----------|------|
| step3_taxonomy | featureSeqs.qza | 物种注释 |
"""


def test_table_style_doc() -> None:
    """Real script-library style: KV table under 基本信息 + file tables under 输入/输出."""
    meta = parse_script_md(DADA2_DOC)
    assert meta.name == "dada2"  # Tool Name row beats the doc title
    assert "ASV" in meta.description
    assert meta.runtime_min == 480.0  # 8.0 小时 → minutes
    assert meta.cost == 14.4

    inputs = json.loads(meta.inputs)
    assert inputs == [
        {"label": "manifest.tsv", "extensions": ["tsv"], "multiple": False},
        {"label": "${sample}.fastq", "extensions": ["fastq"], "multiple": True},
    ]
    outputs = json.loads(meta.outputs)
    assert outputs == [
        {"label": "featureSeqs.qza", "extensions": ["qza"], "multiple": False},
        {"label": "feature-table.tsv", "extensions": ["tsv"], "multiple": False},
    ]
    # unrelated tables (参数说明 / 下游工具) must not leak into slots
    assert all("manifest 文件路径" not in s["label"] for s in inputs)
    assert all("step3_taxonomy" not in s["label"] for s in outputs)
