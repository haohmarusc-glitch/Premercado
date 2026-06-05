import { 
  useListObservations, 
  getListObservationsQueryKey 
} from "@workspace/api-client-react";
import { Card, CardContent } from "@/components/ui/card";
import { formatDateTime } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useState } from "react";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";
import { Button } from "@/components/ui/button";

export default function Observations() {
  const [tickerFilter, setTickerFilter] = useState<string | undefined>(undefined);

  const { data: observations, isLoading } = useListObservations({ ticker: tickerFilter, limit: 100 }, {
    query: {
      queryKey: getListObservationsQueryKey({ ticker: tickerFilter, limit: 100 })
    }
  });

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      <div className="flex items-end justify-between border-b border-border pb-4">
        <div>
          <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight">OBSERVATIONS</h1>
          <p className="text-muted-foreground font-mono text-sm mt-2">Raw memory feed of agent insights</p>
        </div>
        <div className="flex gap-2">
          <Button 
            variant={tickerFilter === undefined ? "default" : "outline"} 
            className="font-mono text-xs rounded-sm h-8"
            onClick={() => setTickerFilter(undefined)}
          >
            ALL
          </Button>
          <Button 
            variant={tickerFilter === "MU" ? "default" : "outline"} 
            className="font-mono text-xs rounded-sm h-8"
            onClick={() => setTickerFilter("MU")}
          >
            MU
          </Button>
          <Button 
            variant={tickerFilter === "SMCI" ? "default" : "outline"} 
            className="font-mono text-xs rounded-sm h-8"
            onClick={() => setTickerFilter("SMCI")}
          >
            SMCI
          </Button>
        </div>
      </div>

      <div className="space-y-3">
        {isLoading ? (
          Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-32 w-full" />
          ))
        ) : !observations?.length ? (
          <div className="p-12 text-center border border-dashed border-border rounded-sm text-muted-foreground font-mono">
            No observations found.
          </div>
        ) : (
          observations.map(obs => (
            <Card key={obs.id} className="bg-card border-border shadow-none rounded-sm">
              <CardContent className="p-4">
                <div className="flex justify-between items-start mb-3">
                  <div className="flex items-center gap-3">
                    <Badge variant="outline" className="font-mono bg-secondary/50 border-border text-primary font-bold">
                      {obs.ticker}
                    </Badge>
                    <span className="text-xs text-muted-foreground font-mono">
                      {formatDateTime(obs.createdAt)}
                    </span>
                    {obs.priceAtObservation && (
                      <span className="text-xs text-muted-foreground font-mono border border-border px-1.5 py-0.5 rounded-sm">
                        ${obs.priceAtObservation.toFixed(2)}
                      </span>
                    )}
                  </div>
                  <div>
                    {obs.sentiment === "bullish" && <Badge variant="outline" className="bg-green-500/10 text-green-500 border-green-500/20 font-mono"><TrendingUp className="w-3 h-3 mr-1"/> BULLISH</Badge>}
                    {obs.sentiment === "bearish" && <Badge variant="outline" className="bg-red-500/10 text-red-500 border-red-500/20 font-mono"><TrendingDown className="w-3 h-3 mr-1"/> BEARISH</Badge>}
                    {obs.sentiment === "neutral" && <Badge variant="outline" className="bg-slate-500/10 text-slate-400 border-slate-500/20 font-mono"><Minus className="w-3 h-3 mr-1"/> NEUTRAL</Badge>}
                  </div>
                </div>
                <div className="font-mono text-sm leading-relaxed text-foreground border-l-2 border-primary/50 pl-3 py-1">
                  {obs.summary}
                </div>
              </CardContent>
            </Card>
          ))
        )}
      </div>
    </div>
  );
}
