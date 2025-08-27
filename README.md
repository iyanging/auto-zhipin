# Auto Zhipin

> Inspired by [ufownl/auto-zhipin](https://github.com/ufownl/auto-zhipin)

## Installation

* `uv sync --frozen`
* `uv run camoufox fetch`

## Usage

* edit `.env`, define
  * `LLM_MODEL`
  * `LLM_API_KEY`

  and `source .env` in your shell

* In your browser, select filters, and get the url, such as `https://www.zhipin.com/web/geek/jobs?city=101210100&jobType=1901&salary=406&experience=106&degree=203&industry=100020&scale=303`

* `uv run auto_zhipin --job-count 128 --from-url {JOB_LIST_URL}`
