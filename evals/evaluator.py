"""
Automated evaluations (8 evaluations, no LLM calls needed).
Scores are computed from telemetry data.
"""
import sqlite3
from datetime import datetime
from dataclasses import dataclass
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class EvalResult:
    """Result of a single evaluation."""
    eval_name: str
    score: float  # 0.0-1.0
    grade: str  # A-F
    details: str
    count: int
    timestamp: str


class Evaluator:
    """Automated evaluations on telemetry data."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize evaluator with config thresholds."""
        self.config = config
    
    def evaluate(self, reader) -> List[EvalResult]:
        """
        Run all 8 evaluations.
        
        Args:
            reader: OpenClawReader instance
        
        Returns:
            List of EvalResult objects
        """
        results = []
        
        results.append(self._eval_error_rate(reader))
        results.append(self._eval_cache_efficiency(reader))
        results.append(self._eval_cost_efficiency(reader))
        results.append(self._eval_tool_utilization(reader))
        results.append(self._eval_abort_rate(reader))
        results.append(self._eval_output_density(reader))
        results.append(self._eval_thinking_usage(reader))
        results.append(self._eval_provider_diversity(reader))
        
        return results
    
    def _eval_error_rate(self, reader) -> EvalResult:
        """Error Rate: A=<2%, B=<5%, C=<10%, D=<20%"""
        stats = reader.get_stats()
        error_rate = stats.get("error_rate", 0.0)
        
        thresholds = self.config.get("eval_thresholds", {}).get("error_rate", {})
        a_threshold = thresholds.get("a", 0.02)
        b_threshold = thresholds.get("b", 0.05)
        c_threshold = thresholds.get("c", 0.10)
        d_threshold = thresholds.get("d", 0.20)
        
        if error_rate <= a_threshold:
            grade = "A"
            score = 1.0
        elif error_rate <= b_threshold:
            grade = "B"
            score = 0.85
        elif error_rate <= c_threshold:
            grade = "C"
            score = 0.70
        elif error_rate <= d_threshold:
            grade = "D"
            score = 0.50
        else:
            grade = "F"
            score = 0.0
        
        count = stats.get("total_calls", 0)
        
        return EvalResult(
            eval_name="Error Rate",
            score=score,
            grade=grade,
            details=f"{error_rate*100:.2f}% error rate ({stats.get('error_count', 0)}/{count} calls)",
            count=count,
            timestamp=datetime.utcnow().isoformat() + "Z",
        )
    
    def _eval_cache_efficiency(self, reader) -> EvalResult:
        """Cache Efficiency: A=>70%, B=>50%, C=>30%"""
        with sqlite3.connect(reader.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT AVG(cache_hit_ratio) FROM records WHERE tokens_total > 0")
            avg_cache_hit = cursor.fetchone()[0] or 0.0
        
        thresholds = self.config.get("eval_thresholds", {}).get("cache_efficiency", {})
        a_threshold = thresholds.get("a", 0.70)
        b_threshold = thresholds.get("b", 0.50)
        c_threshold = thresholds.get("c", 0.30)
        
        if avg_cache_hit >= a_threshold:
            grade = "A"
            score = 1.0
        elif avg_cache_hit >= b_threshold:
            grade = "B"
            score = 0.85
        elif avg_cache_hit >= c_threshold:
            grade = "C"
            score = 0.70
        else:
            grade = "D"
            score = 0.50
        
        stats = reader.get_stats()
        count = stats.get("total_calls", 0)
        
        return EvalResult(
            eval_name="Cache Efficiency",
            score=score,
            grade=grade,
            details=f"{avg_cache_hit*100:.1f}% average cache hit ratio",
            count=count,
            timestamp=datetime.utcnow().isoformat() + "Z",
        )
    
    def _eval_cost_efficiency(self, reader) -> EvalResult:
        """Cost Efficiency: cost per useful output token"""
        stats = reader.get_stats()
        total_cost = stats.get("total_cost", 0.0)
        total_tokens = stats.get("total_tokens", 0)
        
        if total_tokens == 0:
            cost_per_token = 0.0
        else:
            cost_per_token = (total_cost / total_tokens) * 1_000_000
        
        # Grade based on cost per million tokens
        if cost_per_token < 5.0:  # Very efficient
            grade = "A"
            score = 1.0
        elif cost_per_token < 10.0:
            grade = "B"
            score = 0.85
        elif cost_per_token < 20.0:
            grade = "C"
            score = 0.70
        elif cost_per_token < 50.0:
            grade = "D"
            score = 0.50
        else:
            grade = "F"
            score = 0.0
        
        count = stats.get("total_calls", 0)
        
        return EvalResult(
            eval_name="Cost Efficiency",
            score=score,
            grade=grade,
            details=f"${cost_per_token:.2f} per million tokens",
            count=count,
            timestamp=datetime.utcnow().isoformat() + "Z",
        )
    
    def _eval_tool_utilization(self, reader) -> EvalResult:
        """Tool Utilization: % calls using tools"""
        with sqlite3.connect(reader.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM records WHERE has_tool_calls = 1")
            tool_calls = cursor.fetchone()[0]
        
        stats = reader.get_stats()
        total_calls = stats.get("total_calls", 0)
        
        if total_calls == 0:
            tool_pct = 0.0
        else:
            tool_pct = tool_calls / total_calls
        
        thresholds = self.config.get("eval_thresholds", {}).get("tool_utilization", {})
        a_threshold = thresholds.get("a", 0.50)
        b_threshold = thresholds.get("b", 0.30)
        c_threshold = thresholds.get("c", 0.15)
        
        if tool_pct >= a_threshold:
            grade = "A"
            score = 1.0
        elif tool_pct >= b_threshold:
            grade = "B"
            score = 0.85
        elif tool_pct >= c_threshold:
            grade = "C"
            score = 0.70
        else:
            grade = "D"
            score = 0.50
        
        return EvalResult(
            eval_name="Tool Utilization",
            score=score,
            grade=grade,
            details=f"{tool_pct*100:.1f}% of calls use tools ({tool_calls}/{total_calls})",
            count=total_calls,
            timestamp=datetime.utcnow().isoformat() + "Z",
        )
    
    def _eval_abort_rate(self, reader) -> EvalResult:
        """Abort Rate: % of aborted calls (end_turn)"""
        with sqlite3.connect(reader.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM records WHERE stop_reason = 'end_turn'")
            aborts = cursor.fetchone()[0]
        
        stats = reader.get_stats()
        total_calls = stats.get("total_calls", 0)
        
        if total_calls == 0:
            abort_rate = 0.0
        else:
            abort_rate = aborts / total_calls
        
        thresholds = self.config.get("eval_thresholds", {}).get("abort_rate", {})
        a_threshold = thresholds.get("a", 0.02)
        b_threshold = thresholds.get("b", 0.05)
        
        if abort_rate <= a_threshold:
            grade = "A"
            score = 1.0
        elif abort_rate <= b_threshold:
            grade = "B"
            score = 0.85
        elif abort_rate <= 0.10:
            grade = "C"
            score = 0.70
        else:
            grade = "D"
            score = 0.50
        
        return EvalResult(
            eval_name="Abort Rate",
            score=score,
            grade=grade,
            details=f"{abort_rate*100:.2f}% of calls ended early ({aborts}/{total_calls})",
            count=total_calls,
            timestamp=datetime.utcnow().isoformat() + "Z",
        )
    
    def _eval_output_density(self, reader) -> EvalResult:
        """Output Density: output_tokens / total_tokens (higher = more productive)"""
        with sqlite3.connect(reader.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT AVG(CAST(tokens_output AS FLOAT) / tokens_total) FROM records WHERE tokens_total > 0")
            avg_density = cursor.fetchone()[0] or 0.0
        
        # Grade based on output density
        if avg_density >= 0.70:
            grade = "A"
            score = 1.0
        elif avg_density >= 0.50:
            grade = "B"
            score = 0.85
        elif avg_density >= 0.30:
            grade = "C"
            score = 0.70
        else:
            grade = "D"
            score = 0.50
        
        stats = reader.get_stats()
        count = stats.get("total_calls", 0)
        
        return EvalResult(
            eval_name="Output Density",
            score=score,
            grade=grade,
            details=f"{avg_density*100:.1f}% of tokens are outputs (high productivity)",
            count=count,
            timestamp=datetime.utcnow().isoformat() + "Z",
        )
    
    def _eval_thinking_usage(self, reader) -> EvalResult:
        """Thinking Usage: % of calls with reasoning/thinking"""
        with sqlite3.connect(reader.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM records WHERE has_thinking = 1")
            thinking_calls = cursor.fetchone()[0]
        
        stats = reader.get_stats()
        total_calls = stats.get("total_calls", 0)
        
        if total_calls == 0:
            thinking_pct = 0.0
        else:
            thinking_pct = thinking_calls / total_calls
        
        # Thinking is good but shouldn't be overused
        if thinking_pct >= 0.30:
            grade = "A"
            score = 1.0
        elif thinking_pct >= 0.15:
            grade = "B"
            score = 0.85
        elif thinking_pct >= 0.05:
            grade = "C"
            score = 0.70
        elif thinking_pct > 0.0:
            grade = "D"
            score = 0.50
        else:
            grade = "F"
            score = 0.0
        
        return EvalResult(
            eval_name="Thinking Usage",
            score=score,
            grade=grade,
            details=f"{thinking_pct*100:.1f}% of calls use reasoning ({thinking_calls}/{total_calls})",
            count=total_calls,
            timestamp=datetime.utcnow().isoformat() + "Z",
        )
    
    def _eval_provider_diversity(self, reader) -> EvalResult:
        """Provider Diversity: Are multiple providers/fallbacks tested?"""
        with sqlite3.connect(reader.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT provider FROM records")
            providers = [row[0] for row in cursor.fetchall()]
        
        provider_count = len(providers)
        stats = reader.get_stats()
        total_calls = stats.get("total_calls", 0)
        
        # Grade based on provider count and distribution
        if provider_count >= 2:
            grade = "A"
            score = 1.0
            details = f"Using {provider_count} providers (good diversification)"
        elif provider_count == 1:
            grade = "B"
            score = 0.5
            details = f"Using only 1 provider ({providers[0] if providers else 'unknown'})"
        else:
            grade = "F"
            score = 0.0
            details = "No providers configured"
        
        return EvalResult(
            eval_name="Provider Diversity",
            score=score,
            grade=grade,
            details=details,
            count=total_calls,
            timestamp=datetime.utcnow().isoformat() + "Z",
        )
