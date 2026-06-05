import { Routes, Route } from "react-router-dom";
import { Layout } from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import Config from "./pages/Config";
import Logs from "./pages/Logs";
import Portfolio from "./pages/Portfolio";
import Risk from "./pages/Risk";
import StockInsights from "./pages/StockInsights";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/config" element={<Config />} />
        <Route path="/logs" element={<Logs />} />
        <Route path="/portfolio" element={<Portfolio />} />
        <Route path="/insights" element={<StockInsights />} />
        <Route path="/risk" element={<Risk />} />
      </Route>
    </Routes>
  );
}
