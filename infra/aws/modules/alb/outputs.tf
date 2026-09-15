output "alb_arn" { value = aws_lb.this.arn }
output "alb_arn_suffix" { value = aws_lb.this.arn_suffix }
output "alb_dns_name" { value = aws_lb.this.dns_name }
output "alb_zone_id" { value = aws_lb.this.zone_id }
output "security_group_id" { value = aws_security_group.alb.id }
output "api_target_group_arn" { value = aws_lb_target_group.api.arn }
output "frontend_target_group_arn" { value = aws_lb_target_group.frontend.arn }
output "app_listener_arn" { value = local.app_listener_arn }
output "public_url" {
  value = var.acm_certificate_arn != null ? "https://${aws_lb.this.dns_name}" : "http://${aws_lb.this.dns_name}"
}
