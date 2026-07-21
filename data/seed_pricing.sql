-- Providers
INSERT INTO providers (name) VALUES 
('Azure OpenAI'),
('AWS Bedrock'),
('GCP Vertex AI');

-- Models (added budget option for better coverage)
INSERT INTO models (provider_id, model_name, tier) VALUES 
(1, 'GPT-4o', 'mid'),
(1, 'GPT-5', 'flagship'),
(1, 'GPT-4o-mini', 'budget'),
(2, 'Claude Sonnet 5', 'flagship'),
(2, 'Claude Sonnet 4.6', 'mid'),
(2, 'Claude Haiku 4.5', 'budget'),   -- Added for symmetry
(3, 'Gemini 3.1 Pro', 'flagship'),
(3, 'Gemini 2.5 Flash', 'mid');

-- On-demand pricing (unchanged, looks good)
INSERT INTO on_demand_pricing (model_id, input_per_mtok, output_per_mtok, effective_date, expires_date, source_url) VALUES 
(1, 2.50, 10.00, '2026-07-10', NULL, 'https://azure.microsoft.com/pricing/details/cognitive-services/openai-service/'),
(2, 1.25, 10.00, '2026-07-10', NULL, 'https://azure.microsoft.com/pricing/details/cognitive-services/openai-service/'),
(3, 0.15, 0.60, '2026-07-10', NULL, 'https://azure.microsoft.com/pricing/details/cognitive-services/openai-service/'),
(4, 2.00, 10.00, '2026-07-01', '2026-08-31', 'https://aws.amazon.com/bedrock/pricing/'),
(5, 3.00, 15.00, '2026-07-10', NULL, 'https://aws.amazon.com/bedrock/pricing/'),
(6, 1.00, 5.00, '2026-07-10', NULL, 'https://aws.amazon.com/bedrock/pricing/'),  -- Haiku example
(7, 2.00, 12.00, '2026-07-10', NULL, 'https://cloud.google.com/vertex-ai/generative-ai/pricing'),
(8, 0.30, 2.50, '2026-07-10', NULL, 'https://cloud.google.com/vertex-ai/generative-ai/pricing');

-- Committed pricing (fixed + model-specific where applicable)
INSERT INTO committed_pricing (provider_id, model_id, unit_name, hourly_rate, term, throughput_note, effective_date, source_url) VALUES 
(1, 1, 'PTU', 1.00, 'hourly', '~2,500 TPM (model dependent)', '2026-07-10', 'https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/provisioned-throughput'),
(1, 1, 'PTU', 0.3562, '1-month', '~2,500 TPM (model dependent)', '2026-07-10', 'https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/provisioned-throughput'),
(1, 1, 'PTU', 0.3028, '1-year', '~2,500 TPM (model dependent)', '2026-07-10', 'https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/provisioned-throughput'),
(2, 4, 'Model Unit', 44.00, 'hourly', 'Legacy proxy rate', '2026-07-10', 'https://aws.amazon.com/bedrock/pricing/'),
(2, 4, 'Model Unit', 22.00, '6-month', 'Legacy proxy rate', '2026-07-10', 'https://aws.amazon.com/bedrock/pricing/');