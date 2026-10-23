// GENERATED bởi scripts/gen-validate-schema.mjs từ types.ts — KHÔNG sửa tay. Kiểm độ tươi: `npm run test:ui` hoặc `node scripts/gen-validate-schema.mjs --check`.
import type { Shape } from "./validate";

export const SCHEMA: Record<string, Shape> = {
 "config": {
  "nullable": false,
  "kind": "object",
  "props": {
   "snapshot": {
    "optional": false,
    "nullable": false,
    "kind": "object",
    "props": {
     "snapshotId": {
      "optional": false,
      "nullable": false,
      "kind": "string"
     },
     "dataAsOf": {
      "optional": false,
      "nullable": false,
      "kind": "string"
     },
     "generatedAt": {
      "optional": false,
      "nullable": false,
      "kind": "string"
     },
     "claimLevel": {
      "optional": false,
      "nullable": false,
      "kind": "enum",
      "values": [
       "dev_research_exploratory"
      ]
     },
     "datasetVersions": {
      "optional": false,
      "nullable": false,
      "kind": "record",
      "value": {
       "nullable": false,
       "kind": "string"
      }
     },
     "label": {
      "optional": false,
      "nullable": false,
      "kind": "string"
     }
    }
   },
   "horizons": {
    "optional": false,
    "nullable": false,
    "kind": "array",
    "item": {
     "nullable": false,
     "kind": "enum",
     "values": [
      1,
      3
     ]
    }
   },
   "unsupportedHorizons": {
    "optional": false,
    "nullable": false,
    "kind": "array",
    "item": {
     "nullable": false,
     "kind": "object",
     "props": {
      "horizon": {
       "optional": false,
       "nullable": false,
       "kind": "enum",
       "values": [
        1,
        3,
        7,
        14
       ]
      },
      "reason": {
       "optional": false,
       "nullable": false,
       "kind": "enum",
       "values": [
        "model_not_available"
       ]
      }
     }
    }
   },
   "classifier": {
    "optional": false,
    "nullable": false,
    "kind": "object",
    "props": {
     "available": {
      "optional": false,
      "nullable": false,
      "kind": "boolean"
     },
     "reason": {
      "optional": false,
      "nullable": true,
      "kind": "enum",
      "values": [
       "dependency_missing",
       "not_validated"
      ]
     }
    }
   },
   "limits": {
    "optional": false,
    "nullable": false,
    "kind": "object",
    "props": {
     "minGroupSize": {
      "optional": false,
      "nullable": false,
      "kind": "number"
     },
     "positionAlertPct": {
      "optional": false,
      "nullable": false,
      "kind": "number"
     },
     "marketAlertPct": {
      "optional": false,
      "nullable": false,
      "kind": "number"
     }
    }
   }
  }
 },
 "hotels": {
  "nullable": false,
  "kind": "object",
  "props": {
   "snapshotId": {
    "optional": false,
    "nullable": false,
    "kind": "string"
   },
   "hotels": {
    "optional": false,
    "nullable": false,
    "kind": "array",
    "item": {
     "nullable": false,
     "kind": "object",
     "props": {
      "checkIns": {
       "optional": false,
       "nullable": false,
       "kind": "array",
       "item": {
        "nullable": false,
        "kind": "object",
        "props": {
         "date": {
          "optional": false,
          "nullable": false,
          "kind": "string"
         },
         "supportedHorizons": {
          "optional": false,
          "nullable": false,
          "kind": "array",
          "item": {
           "nullable": false,
           "kind": "enum",
           "values": [
            1,
            3
           ]
          }
         }
        }
       }
      },
      "id": {
       "optional": false,
       "nullable": false,
       "kind": "string"
      },
      "name": {
       "optional": false,
       "nullable": false,
       "kind": "string"
      },
      "city": {
       "optional": false,
       "nullable": false,
       "kind": "string"
      },
      "reviewScore": {
       "optional": false,
       "nullable": true,
       "kind": "number"
      },
      "metadataAsOf": {
       "optional": false,
       "nullable": true,
       "kind": "string"
      }
     }
    }
   }
  }
 },
 "forecast": {
  "nullable": false,
  "kind": "object",
  "props": {
   "snapshotId": {
    "optional": false,
    "nullable": false,
    "kind": "string"
   },
   "dataAsOf": {
    "optional": false,
    "nullable": false,
    "kind": "string"
   },
   "hotel": {
    "optional": false,
    "nullable": false,
    "kind": "object",
    "props": {
     "id": {
      "optional": false,
      "nullable": false,
      "kind": "string"
     },
     "name": {
      "optional": false,
      "nullable": false,
      "kind": "string"
     },
     "city": {
      "optional": false,
      "nullable": false,
      "kind": "string"
     },
     "reviewScore": {
      "optional": false,
      "nullable": true,
      "kind": "number"
     },
     "metadataAsOf": {
      "optional": false,
      "nullable": true,
      "kind": "string"
     }
    }
   },
   "checkIn": {
    "optional": false,
    "nullable": false,
    "kind": "string"
   },
   "leadDays": {
    "optional": false,
    "nullable": false,
    "kind": "number"
   },
   "currentPrice": {
    "optional": false,
    "nullable": false,
    "kind": "number"
   },
   "currentObservedAt": {
    "optional": false,
    "nullable": false,
    "kind": "string"
   },
   "inferenceMode": {
    "optional": false,
    "nullable": false,
    "kind": "enum",
    "values": [
     "cold_start",
     "history_enriched"
    ]
   },
   "historyObservationCount": {
    "optional": false,
    "nullable": false,
    "kind": "number"
   },
   "historySpanDays": {
    "optional": false,
    "nullable": true,
    "kind": "number"
   },
   "roomSummary": {
    "optional": false,
    "nullable": false,
    "kind": "object",
    "props": {
     "maxOccupancy": {
      "optional": false,
      "nullable": true,
      "kind": "number"
     },
     "areaM2": {
      "optional": false,
      "nullable": true,
      "kind": "number"
     },
     "breakfastIncluded": {
      "optional": false,
      "nullable": true,
      "kind": "boolean"
     },
     "freeCancellation": {
      "optional": false,
      "nullable": true,
      "kind": "boolean"
     }
    }
   },
   "evidence": {
    "optional": false,
    "nullable": false,
    "kind": "object",
    "props": {
     "level": {
      "optional": false,
      "nullable": false,
      "kind": "enum",
      "values": [
       "dev_exploratory"
      ]
     },
     "claim": {
      "optional": false,
      "nullable": false,
      "kind": "enum",
      "values": [
       "exploratory"
      ]
     }
    }
   },
   "supportedHorizons": {
    "optional": false,
    "nullable": false,
    "kind": "array",
    "item": {
     "nullable": false,
     "kind": "enum",
     "values": [
      1,
      3
     ]
    }
   },
   "unsupportedHorizons": {
    "optional": false,
    "nullable": false,
    "kind": "array",
    "item": {
     "nullable": false,
     "kind": "object",
     "props": {
      "horizon": {
       "optional": false,
       "nullable": false,
       "kind": "enum",
       "values": [
        1,
        3,
        7,
        14
       ]
      },
      "reason": {
       "optional": false,
       "nullable": false,
       "kind": "enum",
       "values": [
        "after_checkin",
        "model_not_available"
       ]
      }
     }
    }
   },
   "predictions": {
    "optional": false,
    "nullable": false,
    "kind": "array",
    "item": {
     "nullable": false,
     "kind": "object",
     "props": {
      "horizon": {
       "optional": false,
       "nullable": false,
       "kind": "enum",
       "values": [
        1,
        3
       ]
      },
      "method": {
       "optional": false,
       "nullable": false,
       "kind": "enum",
       "values": [
        "model",
        "persistence"
       ]
      },
      "routingReason": {
       "optional": false,
       "nullable": false,
       "kind": "string"
      },
      "predictedPrice": {
       "optional": false,
       "nullable": false,
       "kind": "number"
      },
      "expectedDelta": {
       "optional": false,
       "nullable": false,
       "kind": "number"
      },
      "expectedPctChange": {
       "optional": false,
       "nullable": false,
       "kind": "number"
      },
      "direction": {
       "optional": false,
       "nullable": false,
       "kind": "enum",
       "values": [
        "down",
        "stable",
        "up"
       ]
      },
      "targetObservationDate": {
       "optional": false,
       "nullable": false,
       "kind": "string"
      }
     }
    }
   },
   "classifier": {
    "optional": false,
    "nullable": false,
    "kind": "object",
    "props": {
     "available": {
      "optional": false,
      "nullable": false,
      "kind": "boolean"
     },
     "reason": {
      "optional": false,
      "nullable": true,
      "kind": "enum",
      "values": [
       "dependency_missing",
       "not_validated"
      ]
     },
     "results": {
      "optional": false,
      "nullable": false,
      "kind": "array",
      "item": {
       "nullable": false,
       "kind": "object",
       "props": {
        "horizon": {
         "optional": false,
         "nullable": false,
         "kind": "enum",
         "values": [
          1,
          3
         ]
        },
        "label": {
         "optional": false,
         "nullable": false,
         "kind": "enum",
         "values": [
          "down",
          "stable",
          "up"
         ]
        },
        "recordId": {
         "optional": false,
         "nullable": false,
         "kind": "number"
        }
       }
      }
     },
     "claim": {
      "optional": false,
      "nullable": false,
      "kind": "enum",
      "values": [
       "none"
      ]
     }
    }
   },
   "warnings": {
    "optional": false,
    "nullable": false,
    "kind": "array",
    "item": {
     "nullable": false,
     "kind": "object",
     "props": {
      "code": {
       "optional": false,
       "nullable": false,
       "kind": "enum",
       "values": [
        "cold_start_preliminary",
        "group_small",
        "metadata_missing",
        "persistence_routed",
        "review_gap_wide"
       ]
      },
      "message": {
       "optional": false,
       "nullable": false,
       "kind": "string"
      }
     }
    }
   },
   "history": {
    "optional": false,
    "nullable": false,
    "kind": "array",
    "item": {
     "nullable": false,
     "kind": "object",
     "props": {
      "date": {
       "optional": false,
       "nullable": false,
       "kind": "string"
      },
      "price": {
       "optional": false,
       "nullable": false,
       "kind": "number"
      }
     }
    }
   },
   "provenance": {
    "optional": false,
    "nullable": false,
    "kind": "object",
    "props": {
     "canonicalSeriesId": {
      "optional": false,
      "nullable": false,
      "kind": "string"
     },
     "recordId": {
      "optional": false,
      "nullable": false,
      "kind": "number"
     },
     "bundles": {
      "optional": false,
      "nullable": false,
      "kind": "record",
      "value": {
       "nullable": false,
       "kind": "string"
      }
     }
    }
   }
  }
 },
 "hotelier": {
  "nullable": false,
  "kind": "object",
  "props": {
   "snapshotId": {
    "optional": false,
    "nullable": false,
    "kind": "string"
   },
   "dataAsOf": {
    "optional": false,
    "nullable": false,
    "kind": "string"
   },
   "checkIn": {
    "optional": false,
    "nullable": false,
    "kind": "string"
   },
   "anchor": {
    "optional": false,
    "nullable": false,
    "kind": "object",
    "props": {
     "currentPrice": {
      "optional": false,
      "nullable": false,
      "kind": "number"
     },
     "observedAt": {
      "optional": false,
      "nullable": false,
      "kind": "string"
     },
     "pctVsGroupMedian": {
      "optional": false,
      "nullable": true,
      "kind": "number"
     },
     "change7DaysPct": {
      "optional": false,
      "nullable": true,
      "kind": "number"
     },
     "reviewGap": {
      "optional": false,
      "nullable": true,
      "kind": "number"
     },
     "isAnchor": {
      "optional": false,
      "nullable": false,
      "kind": "boolean"
     },
     "id": {
      "optional": false,
      "nullable": false,
      "kind": "string"
     },
     "name": {
      "optional": false,
      "nullable": false,
      "kind": "string"
     },
     "city": {
      "optional": false,
      "nullable": false,
      "kind": "string"
     },
     "reviewScore": {
      "optional": false,
      "nullable": true,
      "kind": "number"
     },
     "metadataAsOf": {
      "optional": false,
      "nullable": true,
      "kind": "string"
     }
    }
   },
   "compset": {
    "optional": false,
    "nullable": false,
    "kind": "array",
    "item": {
     "nullable": false,
     "kind": "object",
     "props": {
      "currentPrice": {
       "optional": false,
       "nullable": false,
       "kind": "number"
      },
      "observedAt": {
       "optional": false,
       "nullable": false,
       "kind": "string"
      },
      "pctVsGroupMedian": {
       "optional": false,
       "nullable": true,
       "kind": "number"
      },
      "change7DaysPct": {
       "optional": false,
       "nullable": true,
       "kind": "number"
      },
      "reviewGap": {
       "optional": false,
       "nullable": true,
       "kind": "number"
      },
      "isAnchor": {
       "optional": false,
       "nullable": false,
       "kind": "boolean"
      },
      "id": {
       "optional": false,
       "nullable": false,
       "kind": "string"
      },
      "name": {
       "optional": false,
       "nullable": false,
       "kind": "string"
      },
      "city": {
       "optional": false,
       "nullable": false,
       "kind": "string"
      },
      "reviewScore": {
       "optional": false,
       "nullable": true,
       "kind": "number"
      },
      "metadataAsOf": {
       "optional": false,
       "nullable": true,
       "kind": "string"
      }
     }
    }
   },
   "groupSummary": {
    "optional": false,
    "nullable": false,
    "kind": "object",
    "props": {
     "insufficient": {
      "optional": false,
      "nullable": false,
      "kind": "boolean"
     },
     "reason": {
      "optional": false,
      "nullable": true,
      "kind": "enum",
      "values": [
       "anchor_review_missing",
       "group_too_small"
      ]
     },
     "n": {
      "optional": false,
      "nullable": false,
      "kind": "number"
     },
     "maxReviewGap": {
      "optional": false,
      "nullable": true,
      "kind": "number"
     },
     "currentMedianPrice": {
      "optional": false,
      "nullable": true,
      "kind": "number"
     },
     "priceRank": {
      "optional": false,
      "nullable": true,
      "kind": "object",
      "props": {
       "rank": {
        "optional": false,
        "nullable": false,
        "kind": "number"
       },
       "of": {
        "optional": false,
        "nullable": false,
        "kind": "number"
       }
      }
     },
     "perHorizon": {
      "optional": false,
      "nullable": false,
      "kind": "array",
      "item": {
       "nullable": false,
       "kind": "object",
       "props": {
        "horizon": {
         "optional": false,
         "nullable": false,
         "kind": "enum",
         "values": [
          1,
          3
         ]
        },
        "nMatched": {
         "optional": false,
         "nullable": false,
         "kind": "number"
        },
        "nModel": {
         "optional": false,
         "nullable": false,
         "kind": "number"
        },
        "nPersistence": {
         "optional": false,
         "nullable": false,
         "kind": "number"
        },
        "currentMedianPrice": {
         "optional": false,
         "nullable": false,
         "kind": "number"
        },
        "predictedMedianPrice": {
         "optional": false,
         "nullable": false,
         "kind": "number"
        },
        "medianPctChange": {
         "optional": false,
         "nullable": false,
         "kind": "number"
        }
       }
      }
     }
    }
   },
   "checkInPrices": {
    "optional": false,
    "nullable": false,
    "kind": "array",
    "item": {
     "nullable": false,
     "kind": "object",
     "props": {
      "checkIn": {
       "optional": false,
       "nullable": false,
       "kind": "string"
      },
      "anchorPrice": {
       "optional": false,
       "nullable": false,
       "kind": "number"
      },
      "groupMedianPrice": {
       "optional": false,
       "nullable": true,
       "kind": "number"
      },
      "n": {
       "optional": false,
       "nullable": false,
       "kind": "number"
      }
     }
    }
   },
   "anchorForecast": {
    "optional": false,
    "nullable": true,
    "kind": "object",
    "props": {
     "snapshotId": {
      "optional": false,
      "nullable": false,
      "kind": "string"
     },
     "dataAsOf": {
      "optional": false,
      "nullable": false,
      "kind": "string"
     },
     "hotel": {
      "optional": false,
      "nullable": false,
      "kind": "object",
      "props": {
       "id": {
        "optional": false,
        "nullable": false,
        "kind": "string"
       },
       "name": {
        "optional": false,
        "nullable": false,
        "kind": "string"
       },
       "city": {
        "optional": false,
        "nullable": false,
        "kind": "string"
       },
       "reviewScore": {
        "optional": false,
        "nullable": true,
        "kind": "number"
       },
       "metadataAsOf": {
        "optional": false,
        "nullable": true,
        "kind": "string"
       }
      }
     },
     "checkIn": {
      "optional": false,
      "nullable": false,
      "kind": "string"
     },
     "leadDays": {
      "optional": false,
      "nullable": false,
      "kind": "number"
     },
     "currentPrice": {
      "optional": false,
      "nullable": false,
      "kind": "number"
     },
     "currentObservedAt": {
      "optional": false,
      "nullable": false,
      "kind": "string"
     },
     "inferenceMode": {
      "optional": false,
      "nullable": false,
      "kind": "enum",
      "values": [
       "cold_start",
       "history_enriched"
      ]
     },
     "historyObservationCount": {
      "optional": false,
      "nullable": false,
      "kind": "number"
     },
     "historySpanDays": {
      "optional": false,
      "nullable": true,
      "kind": "number"
     },
     "roomSummary": {
      "optional": false,
      "nullable": false,
      "kind": "object",
      "props": {
       "maxOccupancy": {
        "optional": false,
        "nullable": true,
        "kind": "number"
       },
       "areaM2": {
        "optional": false,
        "nullable": true,
        "kind": "number"
       },
       "breakfastIncluded": {
        "optional": false,
        "nullable": true,
        "kind": "boolean"
       },
       "freeCancellation": {
        "optional": false,
        "nullable": true,
        "kind": "boolean"
       }
      }
     },
     "evidence": {
      "optional": false,
      "nullable": false,
      "kind": "object",
      "props": {
       "level": {
        "optional": false,
        "nullable": false,
        "kind": "enum",
        "values": [
         "dev_exploratory"
        ]
       },
       "claim": {
        "optional": false,
        "nullable": false,
        "kind": "enum",
        "values": [
         "exploratory"
        ]
       }
      }
     },
     "supportedHorizons": {
      "optional": false,
      "nullable": false,
      "kind": "array",
      "item": {
       "nullable": false,
       "kind": "enum",
       "values": [
        1,
        3
       ]
      }
     },
     "unsupportedHorizons": {
      "optional": false,
      "nullable": false,
      "kind": "array",
      "item": {
       "nullable": false,
       "kind": "object",
       "props": {
        "horizon": {
         "optional": false,
         "nullable": false,
         "kind": "enum",
         "values": [
          1,
          3,
          7,
          14
         ]
        },
        "reason": {
         "optional": false,
         "nullable": false,
         "kind": "enum",
         "values": [
          "after_checkin",
          "model_not_available"
         ]
        }
       }
      }
     },
     "predictions": {
      "optional": false,
      "nullable": false,
      "kind": "array",
      "item": {
       "nullable": false,
       "kind": "object",
       "props": {
        "horizon": {
         "optional": false,
         "nullable": false,
         "kind": "enum",
         "values": [
          1,
          3
         ]
        },
        "method": {
         "optional": false,
         "nullable": false,
         "kind": "enum",
         "values": [
          "model",
          "persistence"
         ]
        },
        "routingReason": {
         "optional": false,
         "nullable": false,
         "kind": "string"
        },
        "predictedPrice": {
         "optional": false,
         "nullable": false,
         "kind": "number"
        },
        "expectedDelta": {
         "optional": false,
         "nullable": false,
         "kind": "number"
        },
        "expectedPctChange": {
         "optional": false,
         "nullable": false,
         "kind": "number"
        },
        "direction": {
         "optional": false,
         "nullable": false,
         "kind": "enum",
         "values": [
          "down",
          "stable",
          "up"
         ]
        },
        "targetObservationDate": {
         "optional": false,
         "nullable": false,
         "kind": "string"
        }
       }
      }
     },
     "classifier": {
      "optional": false,
      "nullable": false,
      "kind": "object",
      "props": {
       "available": {
        "optional": false,
        "nullable": false,
        "kind": "boolean"
       },
       "reason": {
        "optional": false,
        "nullable": true,
        "kind": "enum",
        "values": [
         "dependency_missing",
         "not_validated"
        ]
       },
       "results": {
        "optional": false,
        "nullable": false,
        "kind": "array",
        "item": {
         "nullable": false,
         "kind": "object",
         "props": {
          "horizon": {
           "optional": false,
           "nullable": false,
           "kind": "enum",
           "values": [
            1,
            3
           ]
          },
          "label": {
           "optional": false,
           "nullable": false,
           "kind": "enum",
           "values": [
            "down",
            "stable",
            "up"
           ]
          },
          "recordId": {
           "optional": false,
           "nullable": false,
           "kind": "number"
          }
         }
        }
       },
       "claim": {
        "optional": false,
        "nullable": false,
        "kind": "enum",
        "values": [
         "none"
        ]
       }
      }
     },
     "warnings": {
      "optional": false,
      "nullable": false,
      "kind": "array",
      "item": {
       "nullable": false,
       "kind": "object",
       "props": {
        "code": {
         "optional": false,
         "nullable": false,
         "kind": "enum",
         "values": [
          "cold_start_preliminary",
          "group_small",
          "metadata_missing",
          "persistence_routed",
          "review_gap_wide"
         ]
        },
        "message": {
         "optional": false,
         "nullable": false,
         "kind": "string"
        }
       }
      }
     },
     "history": {
      "optional": false,
      "nullable": false,
      "kind": "array",
      "item": {
       "nullable": false,
       "kind": "object",
       "props": {
        "date": {
         "optional": false,
         "nullable": false,
         "kind": "string"
        },
        "price": {
         "optional": false,
         "nullable": false,
         "kind": "number"
        }
       }
      }
     },
     "provenance": {
      "optional": false,
      "nullable": false,
      "kind": "object",
      "props": {
       "canonicalSeriesId": {
        "optional": false,
        "nullable": false,
        "kind": "string"
       },
       "recordId": {
        "optional": false,
        "nullable": false,
        "kind": "number"
       },
       "bundles": {
        "optional": false,
        "nullable": false,
        "kind": "record",
        "value": {
         "nullable": false,
         "kind": "string"
        }
       }
      }
     }
    }
   },
   "alerts": {
    "optional": false,
    "nullable": false,
    "kind": "array",
    "item": {
     "nullable": false,
     "kind": "object",
     "props": {
      "id": {
       "optional": false,
       "nullable": false,
       "kind": "string"
      },
      "severity": {
       "optional": false,
       "nullable": false,
       "kind": "enum",
       "values": [
        "attention",
        "info"
       ]
      },
      "category": {
       "optional": false,
       "nullable": false,
       "kind": "enum",
       "values": [
        "market",
        "position"
       ]
      },
      "title": {
       "optional": false,
       "nullable": false,
       "kind": "string"
      },
      "description": {
       "optional": false,
       "nullable": false,
       "kind": "string"
      },
      "rule": {
       "optional": false,
       "nullable": false,
       "kind": "object",
       "props": {
        "name": {
         "optional": false,
         "nullable": false,
         "kind": "enum",
         "values": [
          "market_move",
          "position_gap"
         ]
        },
        "threshold": {
         "optional": false,
         "nullable": false,
         "kind": "number"
        },
        "value": {
         "optional": false,
         "nullable": false,
         "kind": "number"
        },
        "horizon": {
         "optional": true,
         "nullable": true,
         "kind": "enum",
         "values": [
          1,
          3
         ]
        }
       }
      }
     }
    }
   },
   "warnings": {
    "optional": false,
    "nullable": false,
    "kind": "array",
    "item": {
     "nullable": false,
     "kind": "object",
     "props": {
      "code": {
       "optional": false,
       "nullable": false,
       "kind": "enum",
       "values": [
        "cold_start_preliminary",
        "group_small",
        "metadata_missing",
        "persistence_routed",
        "review_gap_wide"
       ]
      },
      "message": {
       "optional": false,
       "nullable": false,
       "kind": "string"
      }
     }
    }
   }
  }
 },
 "model-info": {
  "nullable": false,
  "kind": "object",
  "props": {
   "snapshot": {
    "optional": false,
    "nullable": false,
    "kind": "object",
    "props": {
     "snapshotId": {
      "optional": false,
      "nullable": false,
      "kind": "string"
     },
     "dataAsOf": {
      "optional": false,
      "nullable": false,
      "kind": "string"
     },
     "generatedAt": {
      "optional": false,
      "nullable": false,
      "kind": "string"
     },
     "claimLevel": {
      "optional": false,
      "nullable": false,
      "kind": "enum",
      "values": [
       "dev_research_exploratory"
      ]
     },
     "datasetVersions": {
      "optional": false,
      "nullable": false,
      "kind": "record",
      "value": {
       "nullable": false,
       "kind": "string"
      }
     },
     "label": {
      "optional": false,
      "nullable": false,
      "kind": "string"
     }
    }
   },
   "horizons": {
    "optional": false,
    "nullable": false,
    "kind": "array",
    "item": {
     "nullable": false,
     "kind": "object",
     "props": {
      "horizon": {
       "optional": false,
       "nullable": false,
       "kind": "enum",
       "values": [
        1,
        3
       ]
      },
      "task": {
       "optional": false,
       "nullable": false,
       "kind": "enum",
       "values": [
        "regression"
       ]
      },
      "modelName": {
       "optional": false,
       "nullable": false,
       "kind": "string"
      },
      "family": {
       "optional": false,
       "nullable": false,
       "kind": "string"
      },
      "datasetVersion": {
       "optional": false,
       "nullable": false,
       "kind": "string"
      },
      "claimLevel": {
       "optional": false,
       "nullable": false,
       "kind": "enum",
       "values": [
        "dev_research_exploratory"
       ]
      },
      "bundleSha256": {
       "optional": false,
       "nullable": false,
       "kind": "string"
      },
      "trainedEnvironment": {
       "optional": false,
       "nullable": false,
       "kind": "record",
       "value": {
        "nullable": false,
        "kind": "string"
       }
      },
      "validatedRuntime": {
       "optional": false,
       "nullable": false,
       "kind": "record",
       "value": {
        "nullable": false,
        "kind": "string"
       }
      },
      "compatibility": {
       "optional": false,
       "nullable": false,
       "kind": "object",
       "props": {
        "status": {
         "optional": false,
         "nullable": false,
         "kind": "enum",
         "values": [
          "dev_compatibility_empirically_checked"
         ]
        },
        "rows": {
         "optional": false,
         "nullable": false,
         "kind": "number"
        },
        "maxAbsDiffVnd": {
         "optional": false,
         "nullable": false,
         "kind": "number"
        },
        "rowsOverTolerance": {
         "optional": false,
         "nullable": false,
         "kind": "number"
        },
        "toleranceVnd": {
         "optional": false,
         "nullable": false,
         "kind": "number"
        }
       }
      },
      "routing": {
       "optional": false,
       "nullable": false,
       "kind": "array",
       "item": {
        "nullable": false,
        "kind": "object",
        "props": {
         "mode": {
          "optional": false,
          "nullable": false,
          "kind": "enum",
          "values": [
           "cold_start",
           "history_enriched"
          ]
         },
         "useModel": {
          "optional": false,
          "nullable": false,
          "kind": "boolean"
         },
         "reason": {
          "optional": false,
          "nullable": false,
          "kind": "string"
         },
         "validationLiftVnd": {
          "optional": false,
          "nullable": false,
          "kind": "number"
         },
         "ci95": {
          "optional": false,
          "nullable": false,
          "kind": "array",
          "item": {
           "nullable": false,
           "kind": "number"
          }
         },
         "validationRows": {
          "optional": false,
          "nullable": false,
          "kind": "number"
         }
        }
       }
      },
      "evidence": {
       "optional": false,
       "nullable": false,
       "kind": "object",
       "props": {
        "validationLiftPct": {
         "optional": false,
         "nullable": false,
         "kind": "array",
         "item": {
          "nullable": false,
          "kind": "number"
         }
        },
        "testLiftPct": {
         "optional": false,
         "nullable": false,
         "kind": "array",
         "item": {
          "nullable": false,
          "kind": "number"
         }
        },
        "testExposed": {
         "optional": false,
         "nullable": false,
         "kind": "enum",
         "values": [
          true
         ]
        },
        "source": {
         "optional": false,
         "nullable": false,
         "kind": "string"
        },
        "sourceSha256": {
         "optional": false,
         "nullable": false,
         "kind": "string"
        }
       }
      },
      "limitations": {
       "optional": false,
       "nullable": false,
       "kind": "array",
       "item": {
        "nullable": false,
        "kind": "string"
       }
      }
     }
    }
   },
   "unsupportedHorizons": {
    "optional": false,
    "nullable": false,
    "kind": "array",
    "item": {
     "nullable": false,
     "kind": "object",
     "props": {
      "horizon": {
       "optional": false,
       "nullable": false,
       "kind": "enum",
       "values": [
        1,
        3,
        7,
        14
       ]
      },
      "reason": {
       "optional": false,
       "nullable": false,
       "kind": "enum",
       "values": [
        "model_not_available"
       ]
      }
     }
    }
   },
   "classifier": {
    "optional": false,
    "nullable": false,
    "kind": "object",
    "props": {
     "available": {
      "optional": false,
      "nullable": false,
      "kind": "boolean"
     },
     "reason": {
      "optional": false,
      "nullable": true,
      "kind": "enum",
      "values": [
       "dependency_missing",
       "not_validated"
      ]
     },
     "claim": {
      "optional": false,
      "nullable": false,
      "kind": "enum",
      "values": [
       "none"
      ]
     },
     "note": {
      "optional": false,
      "nullable": false,
      "kind": "string"
     },
     "horizons": {
      "optional": false,
      "nullable": false,
      "kind": "array",
      "item": {
       "nullable": false,
       "kind": "object",
       "props": {
        "horizon": {
         "optional": false,
         "nullable": false,
         "kind": "enum",
         "values": [
          1,
          3
         ]
        },
        "task": {
         "optional": false,
         "nullable": false,
         "kind": "enum",
         "values": [
          "classification"
         ]
        },
        "modelName": {
         "optional": false,
         "nullable": false,
         "kind": "string"
        },
        "family": {
         "optional": false,
         "nullable": false,
         "kind": "string"
        },
        "datasetVersion": {
         "optional": false,
         "nullable": false,
         "kind": "string"
        },
        "claimLevel": {
         "optional": false,
         "nullable": false,
         "kind": "enum",
         "values": [
          "none"
         ]
        },
        "bundleSha256": {
         "optional": false,
         "nullable": false,
         "kind": "string"
        },
        "trainedEnvironment": {
         "optional": false,
         "nullable": false,
         "kind": "record",
         "value": {
          "nullable": false,
          "kind": "string"
         }
        },
        "validatedRuntime": {
         "optional": false,
         "nullable": false,
         "kind": "record",
         "value": {
          "nullable": false,
          "kind": "string"
         }
        },
        "classMapping": {
         "optional": false,
         "nullable": false,
         "kind": "record",
         "value": {
          "nullable": false,
          "kind": "string"
         }
        },
        "compatibility": {
         "optional": false,
         "nullable": false,
         "kind": "object",
         "props": {
          "status": {
           "optional": false,
           "nullable": false,
           "kind": "enum",
           "values": [
            "dev_compatibility_empirically_checked"
           ]
          },
          "rows": {
           "optional": false,
           "nullable": false,
           "kind": "number"
          },
          "maxAbsProbabilityDiff": {
           "optional": false,
           "nullable": false,
           "kind": "number"
          },
          "labelMismatchCount": {
           "optional": false,
           "nullable": false,
           "kind": "number"
          },
          "toleranceProbability": {
           "optional": false,
           "nullable": false,
           "kind": "number"
          }
         }
        },
        "coverage": {
         "optional": false,
         "nullable": false,
         "kind": "object",
         "props": {
          "snapshotRecords": {
           "optional": false,
           "nullable": false,
           "kind": "number"
          },
          "recordIdsLinkedToRegression": {
           "optional": false,
           "nullable": false,
           "kind": "enum",
           "values": [
            true
           ]
          }
         }
        },
        "limitations": {
         "optional": false,
         "nullable": false,
         "kind": "array",
         "item": {
          "nullable": false,
          "kind": "string"
         }
        }
       }
      }
     }
    }
   },
   "rules": {
    "optional": false,
    "nullable": false,
    "kind": "array",
    "item": {
     "nullable": false,
     "kind": "object",
     "props": {
      "name": {
       "optional": false,
       "nullable": false,
       "kind": "string"
      },
      "description": {
       "optional": false,
       "nullable": false,
       "kind": "string"
      },
      "threshold": {
       "optional": false,
       "nullable": false,
       "kind": "number"
      }
     }
    }
   },
   "notes": {
    "optional": false,
    "nullable": false,
    "kind": "array",
    "item": {
     "nullable": false,
     "kind": "string"
    }
   }
  }
 }
};
