output "cluster_name" { value = aws_ecs_cluster.this.name }
output "cluster_arn" { value = aws_ecs_cluster.this.arn }
output "tasks_security_group_id" { value = aws_security_group.tasks.id }

output "api_service_name" { value = aws_ecs_service.api.name }
output "worker_service_name" { value = aws_ecs_service.worker.name }
output "frontend_service_name" { value = aws_ecs_service.frontend.name }

output "api_task_family" { value = aws_ecs_task_definition.api.family }
output "worker_task_family" { value = aws_ecs_task_definition.worker.family }
output "frontend_task_family" { value = aws_ecs_task_definition.frontend.family }
output "migrate_task_family" { value = aws_ecs_task_definition.migrate.family }

output "execution_role_arn" { value = aws_iam_role.execution.arn }
output "task_role_arn" { value = aws_iam_role.task.arn }
