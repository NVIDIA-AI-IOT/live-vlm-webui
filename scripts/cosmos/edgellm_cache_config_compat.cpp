// Local compatibility adapter for the missing Edge-LLM 0.11 encoder-cache binding.
// Uses the public ContextCacheConfig struct from the unchanged pinned source.
// No engine, runtime, CUDA, TensorRT, pointer-offset, or binary patching code.
#include <cstddef>
#include <type_traits>
#include <pybind11/pybind11.h>
#include "runtime/state/contextCache/contextCacheConfig.h"

namespace py = pybind11;
using trt_edgellm::rt::ContextCacheConfig;
static_assert(std::is_standard_layout_v<ContextCacheConfig>);
static_assert(sizeof(ContextCacheConfig) == 32);
static_assert(offsetof(ContextCacheConfig, enabled) == 0);
static_assert(offsetof(ContextCacheConfig, maxRecords) == 4);
static_assert(offsetof(ContextCacheConfig, recurrentSnapshotPoolBytes) == 8);
static_assert(offsetof(ContextCacheConfig, partialKvSnapshotPoolBytes) == 16);
static_assert(offsetof(ContextCacheConfig, encoderEmbeddingCacheBudgetBytes) == 24);

py::dict readConfig(ContextCacheConfig const& config)
{
    py::dict result;
    result["enabled"] = config.enabled;
    result["max_records"] = config.maxRecords;
    result["recurrent_snapshot_pool_bytes"] = config.recurrentSnapshotPoolBytes;
    result["partial_kv_snapshot_pool_bytes"] = config.partialKvSnapshotPoolBytes;
    result["encoder_embedding_cache_budget_bytes"] = config.encoderEmbeddingCacheBudgetBytes;
    return result;
}

PYBIND11_MODULE(_edgellm_cache_config_compat, m)
{
    m.doc() = "Typed companion setter for the omitted Edge-LLM 0.11 encoder cache config field";
    m.attr("pybind_internals_id") = PYBIND11_INTERNALS_ID;
    m.attr("edgellm_source_commit") = "95515c2f87fba8982db5a519f9022277667b3cc9";
    m.attr("context_cache_config_size") = sizeof(ContextCacheConfig);
    m.def("read_config", &readConfig, py::arg("config"));
    m.def("disable_encoder_cache", [](ContextCacheConfig& config) {
        constexpr int64_t defaultBudget = 256LL * 1024 * 1024;
        if (config.encoderEmbeddingCacheBudgetBytes != defaultBudget
            && config.encoderEmbeddingCacheBudgetBytes != 0)
        {
            throw std::runtime_error("Unexpected encoder cache default; refuse unsupported native ABI/config");
        }
        config.encoderEmbeddingCacheBudgetBytes = 0;
        return readConfig(config);
    }, py::arg("config"));
}
